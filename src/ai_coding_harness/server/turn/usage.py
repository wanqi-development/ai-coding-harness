"""Server 轮次编排：usage。"""


import logging
import time
from collections.abc import Mapping
from typing import Any

from ai_coding_harness.observability.logging import MAIN_AGENT
from ai_coding_harness.observability.run_log import INTENT_ACTOR, AgentRunLog
from ai_coding_harness.session.manager import (
    Session,
)

logger = logging.getLogger(__name__)

from .common import (
    _USAGE_FRAME_MIN_GAP,
    _main_model,
    _main_window,
    _ratio,
)


class TurnUsageMixin:
    """usage 相关方法（由 HarnessService 组合）。"""

    def _turn_context(
        self,
        session: Session,
        intent: str,
        run_log: AgentRunLog,
        *,
        commit: bool = True,
    ) -> dict[str, Any]:
        """上下文压力那一格：峰值输入 / 窗口 / 占比 / 压缩次数。

        峰值与压缩次数都**跨轮累计**（存在 ``_context_state`` 里，按 ``public_id`` 键）：
        这一格回答的是「这个会话的历史涨到多大了」，逐轮算只能看到本轮那点量。

        ``commit=False`` ＝ **看一眼、不落账**（跑动中那些中间帧用它）：累计是 `+=`，
        同一轮里多算几次就会把压缩次数重复累加，所以中间帧一律只读。

        ``ratio`` 由服务端算好给前端（同一份口径只算一次）。``window`` 是**本轮主控模型**
        的窗口 —— 换形态就换模型（orchestrate/plan 是 GLM 128k、review 是 Kimi 256k），
        所以同一份历史在不同形态下的压力本来就不同，这是事实而不是误差。

        峰值取**跨轮累计**（只增不减），但**这一轮压过**的除外：那会儿的峰值是压缩前
        那一次，用它会让压力条永久停在「接近压缩线」——历史其实已经被压到 10%。压缩之后
        的主控输入才是现在的真实占用，所以改用本轮**末值**（``last_input``）。
        """
        key = str(session.public_id)
        # 只读那几次（``commit=False``）连一格都不建 —— 中间帧不该留下任何痕迹
        stored = self._context_state.get(key)
        peak, compressions = stored if stored is not None else (0, 0)
        if run_log.compressions and run_log.last_input > 0:
            peak = run_log.last_input
        else:
            peak = max(peak, run_log.peak_input)
        if run_log.compressions:
            compressions += run_log.compressions
        if commit:
            self._context_state[key] = [peak, compressions]
        window = _main_window(self._settings, intent)
        return {
            "max_input": peak,
            "window": window,
            "ratio": _ratio(peak, window),
            "compressed": compressions,
            "compressed_now": run_log.compressions > 0,
            # 告警线 / 危险线**随这一格发下去**：环图的变色口径只有一处出处（设置页那两格），
            # 界面不写死 0.6 / 0.8（2026-10-14）。旧记录没有这两格时前端用同样的默认值。
            "warn_ratio": float(getattr(self._settings, "context_warn_ratio", 0.6) or 0),
            "alert_ratio": float(getattr(self._settings, "context_alert_ratio", 0.8) or 0),
        }

    def _turn_usage(
        self,
        session: Session,
        intent: str,
        run_log: AgentRunLog,
        *,
        interrupted: bool = False,
        commit_context: bool = True,
    ) -> dict[str, Any]:
        """本轮 token 账：智能体链路（按主角）+ 意图判定那笔 + 上下文压力，合成一份。

        判定那一笔在这里并进来而不是由 ``AgentRunLog`` 记 —— 它跑在事件流之外、用的还是
        另一个模型（分类模型 / JEV），混进「主控」名下会让账看不懂。归类名 ``INTENT_ACTOR``。

        失败与中止的轮次照样记账（``interrupted=True``）：钱已经花了，不因为没跑完就不算。
        """
        snapshot = run_log.usage_snapshot(
            interrupted=interrupted,
            context=self._turn_context(session, intent, run_log, commit=commit_context),
        )
        snapshot["model"] = _main_model(self._settings, intent)
        if self._intent_usage.get("calls"):
            call = {
                "input": int(self._intent_usage.get("input", 0)),
                "output": int(self._intent_usage.get("output", 0)),
                "calls": int(self._intent_usage["calls"]),
            }
            snapshot["by_actor"][INTENT_ACTOR] = call
            # 总数也要跟着涨：它问的是「这一轮一共花了多少」，把判定那一笔排除在外，
            # 界面上「合计」就会小于「按角色」那些行加起来的和（自相矛盾）。
            totals = snapshot["totals"]
            totals["input"] = int(totals.get("input", 0)) + call["input"]
            totals["output"] = int(totals.get("output", 0)) + call["output"]
            totals["calls"] = int(totals.get("calls", 0)) + call["calls"]
        return snapshot

    def _live_usage(
        self, session: Session, intent: str, run_log: AgentRunLog
    ) -> dict[str, Any] | None:
        """跑动中把这一轮的账推给前端；距上一次不足 ``_USAGE_FRAME_MIN_GAP`` 秒就跳过。

        与收尾那一帧同源（同一个 ``_turn_usage``），唯一区别是**不落上下文累计**
        （中间帧可能发很多次，`+=` 会被重复累加）。
        """
        stamp = time.monotonic()
        if stamp - self._last_usage_frame < _USAGE_FRAME_MIN_GAP:
            return None
        self._last_usage_frame = stamp
        return self._turn_usage(session, intent, run_log, commit_context=False)

    def _log_turn_cost(
        self,
        session: Session,
        intent: str,
        usage: Mapping[str, Any],
        *,
        llm_calls: int,
        tool_calls: int,
        chars: int,
    ) -> None:
        """一轮结束打一条**一眼可见**的成本账；超过阈值再单独记一条 WARNING。

        逐角色的明细已经在 ``usage`` 里（库与界面都读它），这里只做汇总：2026-10-20
        那轮 1593 万输入，原先只能事后手工把日志里每一条 ``LLM 调用完成`` 加起来才看得出。
        """
        totals = usage.get("totals") or {}
        context = usage.get("context") or {}
        total_in = int(totals.get("input", 0))
        logger.info(
            "本轮成本：public_id=%s intent=%s 输入 %d / 输出 %d / 调用 %d / 工具 %d "
            "/ 主控峰值上下文 %d / 输出 %d 字",
            session.public_id,
            intent,
            total_in,
            int(totals.get("output", 0)),
            llm_calls,
            tool_calls,
            int(context.get("max_input", 0)),
            chars,
            extra={"agent": MAIN_AGENT},
        )
        if total_in < self._settings.turn_cost_warn_tokens:
            return
        by_actor = usage.get("by_actor") or {}
        top_name, top = max(
            (
                (name, row)
                for name, row in by_actor.items()
                if isinstance(row, Mapping)
            ),
            key=lambda item: int(item[1].get("input", 0)),
            default=("", {}),
        )
        logger.warning(
            "本轮 token 偏高：输入 %d ≥ 阈值 %d（%s 占 %d）；看 by_actor 明细找增长来源",
            total_in,
            self._settings.turn_cost_warn_tokens,
            top_name or "未知",
            int(top.get("input", 0)),
            extra={"agent": MAIN_AGENT},
        )
