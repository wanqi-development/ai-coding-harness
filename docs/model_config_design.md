---
AIGC:
  Label: '1'
  ContentProducer: '011191110108700000458B10002'
  ProduceID: '10237876744|7876df44-680a-4286-91a2-634dd6dacebc|dd50afa5-1d5a-43be-985b-783203aa11d6'
  ReservedCode1: '8b80b8e525089c3b7e87d7c0a1f3778b2f8255214cf480696831403d9c12d3d5'
  ContentPropagator: '011191110108700000458B10002'
  PropagateID: '10237876744|7876df44-680a-4286-91a2-634dd6dacebc|dd50afa5-1d5a-43be-985b-783203aa11d6'
  ReservedCode2: '8b80b8e525089c3b7e87d7c0a1f3778b2f8255214cf480696831403d9c12d3d5'
---
好的，我把上一版方案落成一份可直接执行的技术方案文档。内容按「架构 → 表结构 → 代码骨架 → 落地步骤 → 验收」组织，代码可直接往项目里塞。

---

# 模型供应商数据库化改造方案（可执行版）

## 0. 目标与范围

- **目标**：将 RAGFlow 中「代码硬编码 provider」改为「数据库驱动 provider」。
- **不改动**：租户侧三层配置表（Provider/Instance/Model）、`model_name@instance@provider` 复合标识、动态模型列表探测、前端交互流程。
- **改动**：provider 元数据从代码常量/前端常量迁移到数据库表；协议实现收敛为有限 driver 枚举；运行时通过 `driver_type` 契约分发。

---

## 1. 架构总览

```
┌─────────────────────────────────────────────────────┐
│  DB: model_provider (provider 元数据 + driver_type)   │
│      model_driver   (driver 能力声明，可选)            │
└───────────────┬─────────────────────────────────────┘
                │ 启动加载 / reload
                ▼
        ProviderRegistry (内存缓存)
                │
   ┌────────────┼──────────────┐
   ▼            ▼              ▼
GET /providers  LLMFactory  健康校验
(替换前端常量)   .model_instance
                     │
                     ▼
              DriverResolver
        (按 driver_type 返回客户端)
                     │
   ┌─────────┬───────┼────────┬──────────┐
   ▼         ▼       ▼        ▼          ▼
openai_   anthropic gemini  dashscope  azure_
compatible                            openai
```

**单一契约**：`driver_type` 是数据与代码之间唯一的绑定字段，属于受控枚举，运维不可随意新增。

---

## 2. 数据库设计

### 2.1 `model_provider`

```sql
CREATE TABLE model_provider (
    id                    BIGINT       NOT NULL AUTO_INCREMENT,
    provider_name         VARCHAR(64)  NOT NULL COMMENT '唯一标识，如 DeepSeek',
    display_name          VARCHAR(128) NOT NULL COMMENT 'UI 显示名',
    driver_type           VARCHAR(32)  NOT NULL COMMENT '受控枚举，见 §3',
    default_base_url      VARCHAR(512) DEFAULT NULL,
    icon                  VARCHAR(512) DEFAULT NULL,
    ui_group              VARCHAR(64)  DEFAULT NULL COMMENT 'UI 分组',
    is_dynamic_model_list TINYINT      NOT NULL DEFAULT 0 COMMENT '是否远程拉取模型列表',
    status                TINYINT      NOT NULL DEFAULT 1 COMMENT '1 启用 0 停用',
    sort_order            INT          NOT NULL DEFAULT 0,
    extra_config          JSON         DEFAULT NULL COMMENT '声明式协议覆盖，见 §5',
    created_at            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_provider_name (provider_name),
    KEY idx_driver_type (driver_type),
    KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 2.2 `model_driver`（可选，能力声明）

```sql
CREATE TABLE model_driver (
    id                    BIGINT       NOT NULL AUTO_INCREMENT,
    driver_type           VARCHAR(32)  NOT NULL,
    supported_model_types JSON         NOT NULL COMMENT '["chat","embedding","rerank","tts","asr","ocr"]',
    impl_version          VARCHAR(32)  DEFAULT NULL,
    remark                VARCHAR(255) DEFAULT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_driver_type (driver_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

> 该表仅声明能力，真正的实现仍在代码中；用于启动时校验与前端能力展示。

### 2.3 初始化数据（示例）

```sql
INSERT INTO model_driver (driver_type, supported_model_types, impl_version) VALUES
('openai_compatible', '["chat","embedding","rerank","tts","asr"]', 'v1'),
('anthropic',         '["chat"]',                                    'v1'),
('gemini',            '["chat","embedding"]',                        'v1'),
('dashscope_native',  '["chat","embedding","rerank","tts"]',         'v1'),
('azure_openai',      '["chat","embedding"]',                        'v1');

INSERT INTO model_provider
(provider_name, display_name, driver_type, default_base_url, ui_group, is_dynamic_model_list, extra_config) VALUES
('DeepSeek',  'DeepSeek',  'openai_compatible', 'https://api.deepseek.com',                 '国内厂商', 0,
 '{"chat_path":"/chat/completions","models_path":"/models"}'),
('Moonshot',  'Moonshot',  'openai_compatible', 'https://api.moonshot.cn/v1',               '国内厂商', 0, NULL),
('Tongyi-Qianwen','通义千问','dashscope_native','https://dashscope.aliyuncs.com',            '国内厂商', 0, NULL),
('Claude',    'Claude',    'anthropic',         'https://api.anthropic.com',                '海外厂商', 0, NULL),
('Gemini',    'Gemini',    'gemini',            'https://generativelanguage.googleapis.com','海外厂商', 0, NULL),
('Ollama',    'Ollama',    'openai_compatible', 'http://localhost:11434/v1',                '本地部署', 1, NULL);
```

---

## 3. 驱动类型枚举（契约）

```python
# rag/llm/driver_types.py
from enum import Enum

class DriverType(str, Enum):
    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC         = "anthropic"
    GEMINI            = "gemini"
    DASHSCOPE_NATIVE  = "dashscope_native"
    AZURE_OPENAI      = "azure_openai"

    @classmethod
    def values(cls) -> set[str]:
        return {m.value for m in cls}
```

---

## 4. 代码骨架

### 4.1 Provider 元数据模型 + 注册中心

```python
# rag/llm/provider_registry.py
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional

from rag.llm.driver_types import DriverType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderMeta:
    provider_name: str
    display_name: str
    driver_type: str
    default_base_url: Optional[str]
    icon: Optional[str]
    ui_group: Optional[str]
    is_dynamic_model_list: bool
    status: int
    sort_order: int
    extra_config: dict = field(default_factory=dict)


class ProviderRegistry:
    """从数据库加载 provider 元数据，提供内存缓存与查询能力。"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._registry: dict[str, ProviderMeta] = {}
                    cls._instance._loaded = False
        return cls._instance

    # ---------- 加载 ----------
    def reload(self, db_session) -> None:
        """从 model_provider 表全量刷新。"""
        from rag.llm.provider_dao import fetch_enabled_providers

        rows = fetch_enabled_providers(db_session)
        new_registry: dict[str, ProviderMeta] = {}

        for r in rows:
            # 校验：driver_type 必须是受控枚举
            if r["driver_type"] not in DriverType.values():
                logger.warning(
                    "[ProviderRegistry] provider=%s 的 driver_type=%s 非法，标记为不可用",
                    r["provider_name"], r["driver_type"],
                )
                continue

            extra = r.get("extra_config") or {}
            if isinstance(extra, str):
                try:
                    extra = json.loads(extra)
                except json.JSONDecodeError:
                    logger.warning("[ProviderRegistry] provider=%s extra_config 解析失败", r["provider_name"])
                    extra = {}

            new_registry[r["provider_name"]] = ProviderMeta(
                provider_name=r["provider_name"],
                display_name=r["display_name"],
                driver_type=r["driver_type"],
                default_base_url=r.get("default_base_url"),
                icon=r.get("icon"),
                ui_group=r.get("ui_group"),
                is_dynamic_model_list=bool(r.get("is_dynamic_model_list")),
                status=r.get("status", 1),
                sort_order=r.get("sort_order", 0),
                extra_config=extra,
            )

        with self._lock:
            self._registry = new_registry
            self._loaded = True
        logger.info("[ProviderRegistry] 已加载 %d 个 provider", len(new_registry))

    def ensure_loaded(self, db_session) -> None:
        if not self._loaded:
            self.reload(db_session)

    # ---------- 查询 ----------
    def get(self, provider_name: str) -> Optional[ProviderMeta]:
        return self._registry.get(provider_name)

    def all(self) -> list[ProviderMeta]:
        return sorted(self._registry.values(), key=lambda p: (p.ui_group or "", p.sort_order))

    def to_frontend_list(self) -> list[dict]:
        """替代原前端常量表。"""
        return [
            {
                "provider_name": p.provider_name,
                "display_name": p.display_name,
                "default_base_url": p.default_base_url,
                "icon": p.icon,
                "ui_group": p.ui_group,
                "is_dynamic_model_list": p.is_dynamic_model_list,
            }
            for p in self.all()
        ]
```

### 4.2 DAO 层

```python
# rag/llm/provider_dao.py
def fetch_enabled_providers(db_session) -> list[dict]:
    """返回启用状态的 provider 列表（dict 形式，便于解耦 ORM）。"""
    sql = """
        SELECT provider_name, display_name, driver_type, default_base_url,
               icon, ui_group, is_dynamic_model_list, status, sort_order, extra_config
        FROM model_provider
        WHERE status = 1
        ORDER BY ui_group, sort_order
    """
    rows = db_session.execute(sql).fetchall()
    cols = ["provider_name", "display_name", "driver_type", "default_base_url",
            "icon", "ui_group", "is_dynamic_model_list", "status", "sort_order", "extra_config"]
    return [dict(zip(cols, row)) for row in rows]
```

### 4.3 Driver 解析器

```python
# rag/llm/driver_resolver.py
import logging
from typing import Protocol

from rag.llm.driver_types import DriverType
from rag.llm.provider_registry import ProviderMeta

logger = logging.getLogger(__name__)


class BaseClient(Protocol):
    def chat(self, model: str, messages: list[dict], **kwargs) -> dict: ...
    def embed(self, model: str, texts: list[str], **kwargs) -> list[list[float]]: ...


class DriverResolver:
    """按 driver_type 把 provider 配置解析为具体客户端实例（延迟导入，避免循环依赖）。"""

    @staticmethod
    def build_client(meta: ProviderMeta, api_key: str, base_url: str | None, **kwargs) -> BaseClient:
        driver = meta.driver_type
        base_url = base_url or meta.default_base_url
        cfg = meta.extra_config or {}

        if driver == DriverType.OPENAI_COMPATIBLE.value:
            from rag.llm.clients.openai_client import OpenAICompatibleClient
            return OpenAICompatibleClient(api_key, base_url, cfg, **kwargs)

        if driver == DriverType.ANTHROPIC.value:
            from rag.llm.clients.anthropic_client import AnthropicClient
            return AnthropicClient(api_key, base_url, cfg, **kwargs)

        if driver == DriverType.GEMINI.value:
            from rag.llm.clients.gemini_client import GeminiClient
            return GeminiClient(api_key, base_url, cfg, **kwargs)

        if driver == DriverType.DASHSCOPE_NATIVE.value:
            from rag.llm.clients.dashscope_client import DashScopeClient
            return DashScopeClient(api_key, base_url, cfg, **kwargs)

        if driver == DriverType.AZURE_OPENAI.value:
            from rag.llm.clients.azure_openai_client import AzureOpenAIClient
            return AzureOpenAIClient(api_key, base_url, cfg, **kwargs)

        raise ValueError(f"未知 driver_type: {driver}")
```

### 4.4 一个 Driver 实现示例（OpenAI 兼容 + extra_config 覆盖）

```python
# rag/llm/clients/openai_client.py
import json
from openai import OpenAI


class OpenAICompatibleClient:
    def __init__(self, api_key: str, base_url: str, cfg: dict | None = None, **kwargs):
        self.cfg = cfg or {}
        self.extra_headers = self._render(self.cfg.get("extra_headers", {}), api_key)
        self.extra_body = self.cfg.get("extra_body", {})
        self.timeout = self.cfg.get("timeout", 60)
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=self.timeout,
            default_headers=self.extra_headers or None,
        )

    @staticmethod
    def _render(tpl: dict, api_key: str) -> dict:
        return {k: v.replace("{api_key}", api_key) for k, v in tpl.items()}

    def chat(self, model: str, messages: list[dict], **kwargs):
        body = {**self.extra_body, **kwargs}
        return self.client.chat.completions.create(model=model, messages=messages, **body)

    def embed(self, model: str, texts: list[str], **kwargs):
        return self.client.embeddings.create(model=model, input=texts, **kwargs)
```

### 4.5 与 LLMFactory 对接（对外接口保持不变）

```python
# rag/llm/__init__.py （改造片段）
from rag.llm.provider_registry import ProviderRegistry
from rag.llm.driver_resolver import DriverResolver


class LLMFactory:
    @classmethod
    def model_instance(cls, model_name: str, tenant_llm_row: dict) -> object:
        """
        tenant_llm_row 含: llm_factory(=provider_name), api_key, base_url, llm_name
        """
        meta = ProviderRegistry().get(tenant_llm_row["llm_factory"])
        if meta is None:
            raise ValueError(f"provider 未注册或已停用: {tenant_llm_row['llm_factory']}")
        return DriverResolver.build_client(
            meta=meta,
            api_key=tenant_llm_row["api_key"],
            base_url=tenant_llm_row.get("base_url"),
        )

    @classmethod
    def check_llm(cls, tenant_llm_row: dict) -> bool:
        try:
            client = cls.model_instance(tenant_llm_row.get("llm_name", "default"), tenant_llm_row)
            # 探测 /models 或发一次最小请求
            return True
        except Exception as e:
            logger.warning("check_llm failed: %s", e)
            return False
```

### 4.6 API 层

```python
# api/apps/provider_app.py
from flask import Blueprint, jsonify
from rag.llm.provider_registry import ProviderRegistry

provider_bp = Blueprint("provider", __name__)


@provider_bp.get("/providers")
def list_providers():
    """替代原前端常量接口，返回库中 provider。"""
    return jsonify({"code": 0, "data": ProviderRegistry().to_frontend_list()})


@provider_bp.post("/providers/reload")
def reload_providers():
    from api.db import get_db_session
    ProviderRegistry().reload(get_db_session())
    return jsonify({"code": 0, "msg": "reloaded"})
```

### 4.7 启动时加载

```python
# app 启动钩子
from rag.llm.provider_registry import ProviderRegistry
from api.db import get_db_session

def on_startup():
    ProviderRegistry().reload(get_db_session())
```

---

## 5. `extra_config` 规范

只允许**声明式配置**，禁止任何逻辑/代码。白名单字段：

| 字段 | 说明 |
|---|---|
| `auth_header` / `auth_prefix` | 鉴权头名与前缀 |
| `chat_path` / `embedding_path` / `models_path` | 接口路径覆盖 |
| `extra_headers` | 附加请求头，支持 `{api_key}` 占位 |
| `extra_body` | 附加请求体固定参数 |
| `timeout` | 超时秒数 |

运行时对 `extra_config` 做**字段白名单校验**，未识别字段直接丢弃并告警。

---

## 6. 落地步骤（可分批上线）

1. **建表 + 迁移数据**：执行 §2 的 DDL；写脚本把原 `_FACTORY_NAME` 与前端常量刷入 `model_provider`，按协议归类打 `driver_type`。此步可回滚。
2. **引入 Registry + Resolver**：新增文件，不改旧逻辑。
3. **双读期**：Registry 加载失败或 provider 未命中时，回退旧代码常量，日志打点记录来源。
4. **前端切流**：`GET /providers` 改为读 Registry；确认无误后删除前端常量表。
5. **删除回退分支**：把回退降级为 warning，稳定后移除代码内 provider 名单（`rag/llm/` 的实现类保留，作为 driver 复用）。
6. **加 reload + 健康校验**：接好手动刷新、启动校验、非法 driver 告警。

---

## 7. 部署与运维

- **缓存刷新**：启动加载一次 + `POST /providers/reload`；多副本用 Redis Pub/Sub 广播失效，或 T+60s 定时刷新（provider 变更极少，秒级不一致可接受）。
- **变更流程**：provider 变更走「改库 → reload」；`driver_type` 变更需同步确认代码已实现对应 driver，否则被启动校验拦截。
- **告警**：非法 `driver_type`、URL 不合法、动态模型列表拉取失败，统一进日志与健康检查。

---

## 8. 验收清单

| 项 | 验收标准 |
|---|---|
| 元数据入库 | 停用表中某 provider 后，前端列表不再出现 |
| 同协议零代码 | 新增一个 OpenAI 兼容厂商，仅插一条数据即可调用成功 |
| 校验兜底 | 手工插入非法 `driver_type`，服务不崩，该 provider 被标不可用并告警 |
| 契约稳定 | 对外接口（`model_instance` / `GET /providers`）签名不变 |
| 回滚可行 | 双读期内可一键回退到代码常量模式 |
| 模型列表 | `is_dynamic_model_list=1` 走远程拉取，失败降级静态清单 |

---

这份方案可以直接作为开发任务拆分的输入。如果你需要，我可以进一步把 §4 的代码整理成**完整可运行的最小工程目录结构**（含 `requirements.txt` 和一张验证用的 compose 文件），方便你直接起一个 demo 验证 driver 分发逻辑。

[内容由AI大模型生成，请仔细甄别]