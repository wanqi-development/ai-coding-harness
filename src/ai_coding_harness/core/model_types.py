"""模型类型，以及「这个名字像哪一类」的判定（2026-10-14 加）。

**为什么要有它**：模型清单现在落表（``provider_models``），每条要带类型 —— 界面上「模型」
下拉只列 ``chat``，而清单本身保留全量（刷新时非文本的也一并记下来，标好类型，需要时切筛选看）。

**判据只能按名字**：OpenAI 兼容的 ``/models`` **只回名字、没有类型字段**（实测那台网关回
888 个名字、一个类型字段都没有），所以用片段匹配。刻意用**片段**而不是整词：厂商命名基本是
``<系列>-<版本>-<档位>``，片段才盖得住 ``gpt-image-2.5-sunburst`` / ``doubao-seedance-2-5`` /
``text-embedding-3-small`` 这些写法。

**反过来的坑更大（误杀文本模型）**，所以凡是「既是文本又是别的」的片段一律不放进来：
``seed``（``doubao-seed-2-1-pro`` 是聊天模型）、``vl`` / ``vision`` / ``omni``（多模态**聊天**）、
``flash`` / ``mini`` / ``lite``（只是档位）、裸 ``wan``（只认 ``wanx`` / ``wan2``）。
"""

from __future__ import annotations

#: 表 ``provider_models.model_type`` 的取值域。界面上「模型」下拉只用 ``chat``，其余是清单的
#: 完整性（将来做代码检索会用到 embedding / rerank）。
MODEL_TYPES: tuple[str, ...] = (
    "chat",
    "embedding",
    "rerank",
    "image",
    "video",
    "audio",
    #: 专用件（审核 / OCR 这类）：不归上面任何一类，但**也不能当 chat** ——
    #: 当 chat 会让它出现在「模型」下拉里。
    "other",
)

MODEL_TYPE_LABELS: dict[str, str] = {
    "chat": "文本",
    "embedding": "向量",
    "rerank": "重排",
    "image": "图像",
    "video": "视频",
    "audio": "语音",
    "other": "专用",
}

#: 类型 → 名字里的特征片段（先匹配到的算数，所以顺序＝优先级）。
_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # 重排要在向量**之前**判：``bge-reranker`` 同时命中 ``bge-``，而它显然不是向量模型
    (
        "rerank",
        ("rerank", "reranker"),
    ),
    (
        "embedding",
        ("embedding", "embed", "bge-", "gte-", "m3e-"),
    ),
    (
        "image",
        (
            "dall-e",
            "gpt-image",
            "imagen",
            "image",
            "banana",
            "seedream",
            "seededit",
            "stable-diffusion",
            "flux",
            "kolors",
            "sd3",
        ),
    ),
    (
        "video",
        ("seedance", "sora", "veo", "kling", "video", "wanx", "wan2"),
    ),
    (
        "audio",
        (
            "tts",
            "audio",
            "speech",
            "voice",
            "realtime",
            "whisper",
            "asr",
            "transcribe",
            "paraformer",
            "sensevoice",
            "cosyvoice",
        ),
    ),
    (
        "other",
        ("ocr", "moderation", "guard"),
    ),
)


def model_type_of(name: str) -> str:
    """这个名字属于哪一类；认不出就是 ``chat``（**默认文本**：认不出时当文本比当成图像有用）。"""
    lowered = (name or "").lower()
    for model_type, markers in _MARKERS:
        if any(marker in lowered for marker in markers):
            return model_type
    return "chat"


def is_text_model(name: str) -> bool:
    """界面上「模型」下拉只列这一类。"""
    return model_type_of(name) == "chat"
