"""按会话读日志：从几个日志文件里挑出「属于某个会话」的行。

**为什么需要这一层**：日志文件是按**来源**分流的（``api.log`` 接口层 / ``agent.log`` 运行时），
而会话维度只在格式串里那一格 ``[session_id]``（2026-10-11 起）与少数 ``public_id=`` 的行上。
所以要按「会话」看日志，得自己挑行；挑完还要把两个文件**按时间合并**成一条时间线，
否则打开面板看到的是「先所有接口日志、再所有运行日志」，看不出因果。

三条口径：

- **只读当前那份文件**，不追 ``.1`` / ``.2`` 备份：把备份也读进来会让「尾部 N 行」的含义
  取决于轮转状态（时多时少，还越来越慢）。要更早的日志去 ``logs/`` 目录拿 —— 面板上有下载。
- **命中规则只有一条：行里出现了会话 id**。它同时覆盖几种写法 —— ``[<id>]``（格式里的会话
  格）、``/sessions/<id>`` 与 ``/chat/<id>``（接口请求行只带路径、不带会话格）、
  ``public_id=<id>``（老行），以及**裸 id**（traceback、异常文本里提到会话）。
  一开始按「带装饰的几种写法」逐个匹配，结果连 traceback 的续行都漏了 —— 单测当场抓到。
- **两类噪声默认滤掉**（面板上有开关的那类可以再打开）：
  1. **日志接口自己的请求**（路径含 ``/sessions/<id>/logs``）—— **永远滤掉**，没有开关。
     否则「看日志」这个动作本身就在往日志里写，每看一次多几行；实测我自己验证时调了 ~55 次，
     给那个会话留下 166 行，正好把要看的东西淹了。
  2. **HTTP 请求噪声**（``api.middleware`` + ``uvicorn.access``）—— 默认滤掉，可开关。一个请求
     打三行（``→`` / ``uvicorn.access`` / ``←``），而前端每 1.5 秒轮询一次工作区状态；实测某会话
     324 行里 **247 行（76%）**是它，真正有用的运行日志只有 62 行。
- **级别是阈值**（``level`` 给 WARNING 就是 WARNING 及以上），不是精确匹配 —— 想排障时
  「只看警告以上」才是常见诉求。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: 日志行的开头：``ts [LEVEL] [trace] [session] [agent] logger: 消息``。
#: 末尾那个 logger 名抓下来是为了认「HTTP 请求噪声」—— 消息里没有别的可靠标识。
#: 认不出开头（续行、第三方库的裸输出）就按「没有级别、没有 logger」处理。
_LINE_HEAD = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \[(?P<level>[A-Z]+)\] "
    r"\[[^\]]*\] \[[^\]]*\] \[[^\]]*\] (?P<logger>[^:]+):"
)

#: handoff 内层日志带的 run 父子标记，嵌在消息正文里：
#: ``handoff [run=call_x parent=call_handoff] LLM 调用完成…``。
_RUN_META = re.compile(
    r"\[run=(?P<run_id>[^\s\]]+) parent=(?P<parent_run_id>[^\s\]]+)\]"
)

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}

#: HTTP 请求噪声的 logger：``api.middleware`` 每个请求两行（→ / ←），``uvicorn.access`` 再来一行。
#: 打开面板第一眼要看到「这个会话干了什么」，而不是浏览器轮询了多少次 —— 默认滤掉。
HTTP_NOISE_LOGGERS = frozenset({"ai_coding_harness.api.middleware", "uvicorn.access"})


@dataclass(frozen=True)
class LogLine:
    """一行日志。``text`` 是**原始整行**（前端直接显示，不再自己解析格式）。"""

    ts: str
    level: str
    #: 打这行的 logger 名（认不出就是空串）—— 用来认 HTTP 请求噪声。
    logger: str
    text: str
    run_id: str = ""
    parent_run_id: str = ""


@dataclass(frozen=True)
class LogSlice:
    """一次读取的结果：``total`` 是**命中总数**，``lines`` 是（可能被 ``limit`` 截过的）尾部。

    两个数分开给，界面才能说清「这是最后 300 行 / 共 1240 行」——只给尾部长度的话，
    用户没法知道上面还有多少。
    """

    total: int
    lines: list[LogLine]


def _is_log_endpoint_line(line: str, session_id: str) -> bool:
    """这行是不是「日志接口自己」的请求？是就永远不显示（看日志不该污染日志）。"""
    return f"/sessions/{session_id}/logs" in line


def _matches(line: str, session_id: str) -> bool:
    """行里出现了这个会话 id 就算它的日志。

    会话 id 是 uuid（36 字符），当子串匹配不会误伤；而按装饰逐个匹配会漏 —— 比如
    traceback 的续行里只有裸 id。这条规则也顺带认下了 ``public_id=<id>`` 这种老写法。
    """
    return session_id in line


def _threshold(level: str | None) -> int:
    """级别阈值；认不出的级别按 INFO（比它严重的照常显示）。"""
    return _LEVELS.get((level or "").strip().upper(), 0)


def _parse(line: str) -> LogLine:
    found = _LINE_HEAD.match(line)
    if found is None:
        # 认不出开头（续行、第三方库的裸输出）：原样留着，级别留空让前端按普通行画
        run_meta = _RUN_META.search(line)
        return LogLine(
            ts="",
            level="",
            logger="",
            text=line,
            run_id=run_meta.group("run_id") if run_meta else "",
            parent_run_id=run_meta.group("parent_run_id") if run_meta else "",
        )
    run_meta = _RUN_META.search(line)
    return LogLine(
        ts=found.group("ts"),
        level=found.group("level"),
        logger=found.group("logger").strip(),
        text=line,
        run_id=run_meta.group("run_id") if run_meta else "",
        parent_run_id=run_meta.group("parent_run_id") if run_meta else "",
    )


def read_session_logs(
    paths: Sequence[Path],
    session_id: str,
    *,
    limit: int | None = None,
    level: str | None = None,
    query: str | None = None,
    with_http: bool = False,
) -> LogSlice:
    """挑出属于 ``session_id`` 的日志行，按时间排好；``limit`` 只留**最后** N 行。

    文件不存在（还没写过日志、或刚被清掉）当作空文件，不抛 —— 面板上「这个会话还没日志」
    是正常的空态。单个文件读失败也只记一条 warning 跳过，不让它把整个面板打成错误。

    ``query`` 是**关键字**（大小写不敏感的子串），``with_http=True`` 才把 HTTP 请求噪声算进来。
    两个条件都在 ``limit`` **之前**生效 —— 否则「搜出 0 行的那屏」只是因为尾部那 300 行里没有，
    而不是整个会话里没有。
    """
    floor = _threshold(level)
    needle = (query or "").strip().lower()
    picked: list[LogLine] = []

    for path in paths:
        try:
            # 逐行读：日志文件到 MB 级，整份读进来只是为了筛几行不划算
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                last_ts = ""
                for raw in handle:
                    line = raw.rstrip("\n")
                    if not line:
                        continue
                    item = _parse(line)
                    # 续行（多行 message、第三方库的裸输出）没有自己的时间戳：沿用它上面那条 ——
                    # 它本来就是这个条目的一部分，给空串会让它排到最前、还可能被 tail 丢掉
                    if item.ts:
                        last_ts = item.ts
                    elif last_ts:
                        item = LogLine(
                            ts=last_ts,
                            level=item.level,
                            logger=item.logger,
                            text=item.text,
                            run_id=item.run_id,
                            parent_run_id=item.parent_run_id,
                        )
                    if not _matches(line, session_id):
                        continue
                    # 日志接口自己的请求：永远不显示（且没有开关 —— 看日志不该污染日志）
                    if _is_log_endpoint_line(line, session_id):
                        continue
                    if not with_http and item.logger in HTTP_NOISE_LOGGERS:
                        continue
                    if floor and _LEVELS.get(item.level, 0) < floor:
                        continue
                    if needle and needle not in line.lower():
                        continue
                    picked.append(item)
        except FileNotFoundError:
            continue
        except OSError:
            logger.warning("读日志文件失败（面板按缺这个文件处理）：%s", path, exc_info=True)

    # 时间戳是 ``YYYY-mm-dd HH:MM:SS``，字典序即时间序。排序稳定，同一秒内的两行保持文件内
    # 顺序（续行也因此紧跟着它所属的那条）。
    picked.sort(key=lambda item: item.ts)
    total = len(picked)
    if limit is not None and limit > 0:
        picked = picked[-limit:]
    return LogSlice(total=total, lines=picked)
