"""凭据脱敏：离开可信区的文本都要先过一遍 ``redact()``。

出口各调各的（工具返回值进 state 前、模型输出进 state 前、日志落盘前、SSE 下发前、
直接执行路径返回前），判断口径只在这一份里，免得几处各写一套慢慢跑偏。

匹配两条腿：

- **值匹配**：调用方把本轮已知的凭据值（沙箱里那份会话 Gitee 令牌）传进来精确替换。
  零误伤，覆盖的是模型真能读到的那一个。先值匹配、后模式匹配 —— 值没被换掉才轮到猜。
- **模式匹配**：只认**锚定上下文**（已知凭据前缀、``Bearer`` 之后、URL 用户信息、
  键名像凭据的赋值右侧）。裸的高熵长串一律不动：git SHA、sha256、正常 base64 在代码与
  diff 里到处都是，靠猜着打会把正常输出打花，那比漏一点更伤 —— 模型看不懂 diff 就没法干活。

同一张表还供 ``detect_credential()`` 用：入口拦截靠它判断一条用户消息能不能收。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import NamedTuple

REDACTED = "[已脱敏]"

# 短于这个长度的值不进词表：空串和「a」这类短值会把正常文本一路替换掉
_MIN_TERM_LENGTH = 8

# 按值命中的类别名。值匹配是精确比对，不存在误判，所以永远排在最前
CRED_TERM = "本会话凭据"


class Rule(NamedTuple):
    """一条脱敏规则。

    ``label`` 是类别名，供入口拒绝时的回执使用 —— 只告诉用户命中了哪一类，绝不含命中的值。
    ``template`` 里的 ``\\1`` 保留「上下文部分」：方案名、用户名、键名本身不是凭据，
    留着才看得懂被打码的是哪一处。
    """

    label: str
    pattern: re.Pattern[str]
    template: str


_RULES: tuple[Rule, ...] = (
    # 已知凭据前缀：GitHub / GitLab / OpenAI / Slack
    Rule(
        "已知凭据前缀",
        re.compile(
            r"\b(?:ghp_|gho_|ghu_|ghs_|github_pat_|glpat-|xox[bp]-|sk-)[A-Za-z0-9_\-]{8,}"
        ),
        REDACTED,
    ),
    # 云厂商密钥：AWS / 阿里云 / Google
    Rule("云厂商密钥", re.compile(r"\bAKIA[0-9A-Z]{12,}"), REDACTED),
    Rule("云厂商密钥", re.compile(r"\bLTAI[A-Za-z0-9]{12,}"), REDACTED),
    Rule("云厂商密钥", re.compile(r"\bAIza[A-Za-z0-9_\-]{20,}"), REDACTED),
    # Authorization: Bearer xxx —— 方案名保留
    Rule(
        "Bearer 令牌",
        re.compile(r"(?i)\b(bearer)(\s+)([A-Za-z0-9_\-\.=]{8,})"),
        r"\1\2" + REDACTED,
    ),
    # URL 用户信息 scheme://user:pass@host —— 用户名与主机保留，只打码口令段
    Rule(
        "URL 内嵌口令",
        re.compile(r"\b([a-z][a-z0-9+.\-]*://[^/\s:@]+:)([^/\s@]{8,})(?=@)"),
        r"\1" + REDACTED,
    ),
    # 赋值右侧是带引号的字面量：token = "xxx" / "api_key": "xxx"（.env、compose、配置清单）
    Rule(
        "赋值字面量",
        re.compile(
            r"(?i)(?<![A-Za-z0-9])(token|secret|password|passwd|api[_-]?key|access[_-]?key|client[_-]?secret)"
            r"(\s*[:=]\s*)([\"'])([^\"'\n]{8,})\3"
        ),
        r"\1\2\3" + REDACTED + r"\3",
    ),
    # 赋值右侧是无引号的字面量：export GIT_TOKEN=xxx（Gitee / GitHub 的令牌同理）
    # 字符集不含点与括号，且右侧不能紧跟 . ( [ —— 这样 `api_key = os.getenv("X")`、
    # `access_key = settings.oss_key`、`password: Optional[str]` 这些代码不会被误伤。
    Rule(
        "赋值字面量",
        re.compile(
            r"(?i)(?<![A-Za-z0-9])(token|secret|password|passwd|api[_-]?key|access[_-]?key|client[_-]?secret)"
            r"(\s*[:=]\s*)([A-Za-z0-9_\-]{8,})(?![\[\(\.])"
        ),
        r"\1\2" + REDACTED,
    ),
)


def secret_terms(
    *values: str | None, min_length: int = _MIN_TERM_LENGTH
) -> tuple[str, ...]:
    """把候选凭据值过滤成词表：空值、过短的值、重复值都丢掉。

    值为 ``None``（未配置）或空串（没注入令牌）是常态，不在这里挡掉的话，
    ``replace("")`` 会把整段文本切碎。
    """
    terms: list[str] = []
    for value in values:
        if isinstance(value, str) and len(value) >= min_length and value not in terms:
            terms.append(value)
    return tuple(terms)


def mask_secret(value: str | None) -> str:
    """凭据打码：``sk-…e2b7``。太短就整段星号 —— 界面只用来「知道填没填」，不回明文。

    界面侧的**唯一**用途是「回显告诉用户这里配过一个值」；保存之后的响应不再带它
    （见 ``api/routes/app_settings.py``）。
    """
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}…{value[-4:]}"


def detect_credential(text: str, terms: Iterable[str] = ()) -> str | None:
    """这段文本里是否含凭据：含则返回类别名，不含返回 None。

    给入口拦截用。判定走**全部**规则，所以能通过检定的文本必然没有任何规则命中项 ——
    一旦放行就不必再对输入补一遍脱敏（那是空操作）。

    返回值只用于告知用户「命中了哪一类」，**绝不回传命中的值**：回执、日志、落库都不该出现它。
    """
    if not text:
        return None
    for term in terms:
        if term and term in text:
            return CRED_TERM
    for rule in _RULES:
        if rule.pattern.search(text):
            return rule.label
    return None


def redact(text: str, terms: Iterable[str] = ()) -> str:
    """脱敏：先按词表精确替换，再按锚定上下文打码。``text`` 为空或缺省词表都安全。"""
    if not text:
        return text
    for term in terms:
        if term and term in text:
            text = text.replace(term, REDACTED)
    for rule in _RULES:
        text = rule.pattern.sub(rule.template, text)
    return text
