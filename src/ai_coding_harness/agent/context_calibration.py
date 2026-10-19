"""在线校准：用供应商回的真值，给字符估算乘一个**按模型**维护的系数。

为什么需要：``context_token_counter`` 是字符近似（中文 1.6、其余 3.8 字/token）。
同一套常量在不同 tokenizer 上偏差完全不同 —— 实测 deepseek-v4-pro 上低估约 5%，
deepseek-flash 上低估约 28%~32%。固定系数补不了这种差异。

做法：每次模型调用回来，拿 ``usage_metadata.input_tokens``（真值）与**那一次实际发出去
的消息**的估算比，按 ``(base_url, model)`` 做 EMA（α=0.3）；下一轮估算直接乘这个系数。
只取估算 ≥ 1000 tokens 的样本 —— 小请求被固定开销主导，比例没有参考价值。

为什么不用上游的 ``use_usage_metadata_scaling``：它拿「最后一条 AI 消息的 total_tokens /
截止那条消息的估算」当比例；而我们的应急裁剪会改写**历史**消息，剪过的历史与那笔真值
对不上，系数会被放大。这里只认我们自己刚发出去的那一份。

系数只存进程内：后端重启后重新学，通常几次调用就收敛。真值缺失（模型不返回用量）时
始终返回 1.0，压缩与裁剪退回原来的字符估算，不会因为「没样本」而失效。
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from ai_coding_harness.agent.middleware.context_trim import context_token_counter

logger = logging.getLogger(__name__)

# 查校准走这个关键字：grep CONTEXT_CALIBRATION logs/agent.log
CALIBRATION_LOG_TAG = "CONTEXT_CALIBRATION"

#: EMA 的步长：新样本占 30%。太小追不上模型切换，太大被单次异常样本带偏。
_ALPHA = 0.3

#: 低于这个估算的样本不记：固定开销（每条消息 3 tokens、工具 schema）占比太大。
_MIN_SAMPLE_TOKENS = 1_000

#: 单个样本与最终系数的夹取范围。1.0 是「不校准」；3.0 已经能覆盖实测最坏情况。
_MIN_RATIO, _MAX_RATIO = 0.5, 3.0

#: 每 N 个样本强制留一条日志（另外首样本与系数明显变化时也留）。
_LOG_EVERY = 10


@dataclass(frozen=True)
class Calibration:
    """一个模型的校准状态：``ratio`` 是乘在字符估算上的系数。"""

    ratio: float
    samples: int


class CalibrationStore:
    """``(base_url, model) → 系数`` 的进程内账本（线程安全，异步并发下共用一份）。"""

    def __init__(
        self,
        *,
        alpha: float = _ALPHA,
        min_sample_tokens: int = _MIN_SAMPLE_TOKENS,
        low: float = _MIN_RATIO,
        high: float = _MAX_RATIO,
    ) -> None:
        self._alpha = alpha
        self._min_sample_tokens = min_sample_tokens
        self._low = low
        self._high = high
        self._data: dict[str, Calibration] = {}
        self._lock = threading.Lock()

    def ratio(self, key: str) -> float:
        """当前系数；没有样本就是 1.0（＝不校准）。"""
        with self._lock:
            found = self._data.get(key)
            return found.ratio if found is not None else 1.0

    def snapshot(self) -> dict[str, Calibration]:
        with self._lock:
            return dict(self._data)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def record(
        self,
        key: str,
        *,
        estimated: int,
        actual: int,
        agent: str = "",
        model: str = "",
    ) -> Calibration | None:
        """记一个样本。``estimated`` 是**未校准**的原始估算。"""
        if not key or estimated < self._min_sample_tokens or actual <= 0:
            return None
        sample = actual / estimated
        if not math.isfinite(sample):
            return None
        sample = min(self._high, max(self._low, sample))

        with self._lock:
            previous = self._data.get(key)
            ratio = (
                sample
                if previous is None
                else (1.0 - self._alpha) * previous.ratio + self._alpha * sample
            )
            ratio = min(self._high, max(self._low, ratio))
            current = Calibration(ratio=ratio, samples=(previous.samples if previous else 0) + 1)
            self._data[key] = current

        moved = (
            previous is not None
            and previous.ratio > 0
            and abs(current.ratio - previous.ratio) / previous.ratio >= 0.05
        )
        if current.samples == 1 or current.samples % _LOG_EVERY == 0 or moved:
            logger.info(
                "%s model=%s agent=%s ratio=%.3f samples=%d sample=%d/%d",
                CALIBRATION_LOG_TAG,
                model or key,
                agent or "-",
                current.ratio,
                current.samples,
                actual,
                estimated,
            )
        return current


def model_key(model: BaseChatModel | None) -> str:
    """校准的键：网关地址 + 模型名。tokenizer 是模型属性，换了模型就该重新学。"""
    base = str(getattr(model, "openai_api_base", "") or "").rstrip("/")
    name = str(getattr(model, "model_name", "") or "")
    if base and name:
        return f"{base}|{name}"
    return name or base


def _usage_of(response: Any) -> tuple[int, str] | None:
    """从模型响应里取 ``(输入 tokens, 实际回答的模型名)``；没有用量就返回 ``None``。"""
    # 压缩路径回来的是 ExtendedModelResponse，真正的模型响应在 model_response 里
    payload = getattr(response, "model_response", response)
    messages = getattr(payload, "result", None) or []
    for message in reversed(messages):
        usage = getattr(message, "usage_metadata", None)
        if not usage:
            continue
        value = usage.get("input_tokens") or usage.get("prompt_tokens")
        if not isinstance(value, int) or value <= 0:
            continue
        metadata = getattr(message, "response_metadata", None) or {}
        return value, str(metadata.get("model_name") or "")
    return None


class CalibratedTokenCounter:
    """压缩中间件用的计数器：先按字符估算，再乘当前系数；顺带记样本。

    上游把它当普通可调用对象用（``token_counter(messages, tools=...)``），所以这里保持
    同样的签名。``record`` 由 ``HarnessSummarizationMiddleware`` 在拿到响应后调用。
    """

    def __init__(
        self,
        model: BaseChatModel | None,
        *,
        agent: str = "",
        store: CalibrationStore | None = None,
    ) -> None:
        self._key = model_key(model)
        self._model_name = str(getattr(model, "model_name", "") or "")
        self._agent = agent
        self._store = store or CALIBRATION
        self._mismatch_logged = False

    @property
    def key(self) -> str:
        return self._key

    def __call__(self, messages: Any, *, tools: Any = None) -> int:
        raw = context_token_counter(messages, tools=tools)
        if not self._key:
            return raw
        return math.ceil(raw * self._store.ratio(self._key))

    def record(self, response: Any, estimated: int) -> Calibration | None:
        """记一次「估算 vs 真值」。``estimated`` 是**校准后**的值（即刚发出去那份的估算）。"""
        if not self._key:
            return None
        found = _usage_of(response)
        if found is None:
            return None
        actual, answered_by = found
        # 兜底模型答的（主导失败）不能记到主导模型的账上
        if answered_by and self._model_name and answered_by != self._model_name:
            if not self._mismatch_logged:
                # 只提示一次：网关回别名（如 deepseek-chat）时这里会一直不匹配，
                # 没有这行就只能看到「校准一直不生效」而不知道为什么。
                self._mismatch_logged = True
                logger.info(
                    "%s 跳过样本：回答模型 %s 与配置模型 %s 不同（可能是兜底或网关别名）",
                    CALIBRATION_LOG_TAG,
                    answered_by,
                    self._model_name,
                )
            return None
        ratio = self._store.ratio(self._key)
        raw = estimated / ratio if ratio > 0 else estimated
        return self._store.record(
            self._key,
            estimated=round(raw),
            actual=actual,
            agent=self._agent,
            model=self._model_name,
        )


#: 默认那份账本：跨角色共享（同一模型在 main / coder 上的 tokenizer 是同一个）。
CALIBRATION = CalibrationStore()


def reset_calibration() -> None:
    """清空默认账本（单测用：模块级状态会跨用例串味）。"""
    CALIBRATION.clear()
