"""把本轮意图与当前时间转成指令，注入主智能体的模型调用。

意图是每轮请求的（``configurable.intent``，由 server 层注入），而智能体是每会话
构建一次并缓存的（``SessionManager.get_agent``），所以意图不能写进构建期的 system
prompt —— 只能在这里逐次注入。注入只作用于本次模型调用，不写入对话历史与 checkpoint。

当前时间同理必须逐次注入（智能体是长期存活对象，构建期取的日期会一路陈旧）：模型写
记忆、写变更记录都要用它，而沙箱里的日期它不一定想得到去取 —— 实测中就编出过一个
并不存在的日期。故不区分意图，每次调用都注入。

只实现异步钩子 ``awrap_model_call``：服务端统一走 ``astream_events``。只写同步版
（``wrap_model_call``）的话，异步调用会落到基类的 ``NotImplementedError``。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

from ai_coding_harness.agent.prompts import intent_directive
from ai_coding_harness.tools.context import get_intent

# 平台面向国内 Gitee 用户，日志与记忆里的日期都按这个时区口径
_TIMEZONE = ZoneInfo("Asia/Shanghai")


def current_time_block() -> str:
    """注入用的当前时间块（精确到分钟，够填记忆模板的 ``{YYYY-MM-DD HH:mm}``）。"""
    now = datetime.now(_TIMEZONE)
    return f"## 当前时间\n- {now:%Y-%m-%d %H:%M}（Asia/Shanghai）"


def now_stamp() -> str:
    """``YYYY-MM-DD HH:mm`` —— 写进记忆「更新于」列的那个口径。

    与注入的时间块同一时区、同一精度：两处各取一次时钟会差出几十毫秒无所谓，
    差一个时区就是记忆里的日期整体错一天。
    """
    return f"{datetime.now(_TIMEZONE):%Y-%m-%d %H:%M}"


class IntentMiddleware(AgentMiddleware):
    """在系统消息末尾追加当前时间与本轮意图指令。"""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        base = request.system_message
        parts = (
            base.text if base else "",
            current_time_block(),
            intent_directive(get_intent()),
        )
        text = "\n\n".join(part for part in parts if part)
        return await handler(request.override(system_message=SystemMessage(content=text)))
