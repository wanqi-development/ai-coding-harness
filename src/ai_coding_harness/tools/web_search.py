"""Web search tool backed by Zhipu AI (智谱) web search."""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from ai_coding_harness.config import Settings


def build_web_search_tool(settings: Settings) -> BaseTool:
    """Build the ``web_search_tool`` bound to settings.

    智谱 SDK（`zai-sdk`）**懒加载**：即使它缺失/损坏，也不影响后端启动。
    """

    @tool
    def web_search_tool(query: str) -> str:
        """用网络搜索引擎检索信息，用于回答一般性/知识性问题。

        Args:
            query: 搜索查询语句。
        """
        if not settings.glm_api_key:
            return "搜索失败：未配置 GLM_API_KEY（请在 .env 中配置）。"

        try:
            from zai import ZhipuAiClient
            from zai.core._errors import (
                APIConnectionError,
                APIResponseError,
                APIStatusError,
                APITimeoutError,
                ZaiError,
            )
        except ImportError as exc:
            return f"搜索失败：无法导入智谱 SDK（{exc}）。请安装 `pip install zai-sdk`。"

        client = ZhipuAiClient(
            api_key=settings.glm_api_key,
            base_url=settings.glm_base_url or None,
        )
        try:
            response = client.web_search.web_search(
                search_engine="search_pro",
                search_query=query,
            )
        except APITimeoutError:
            return "搜索失败：请求超时，请稍后重试。"
        except APIConnectionError:
            return "搜索失败：无法连接搜索服务，请检查网络或 GLM_BASE_URL。"
        except APIStatusError as exc:
            return f"搜索失败：API 状态错误（HTTP {exc.status_code}）。"
        except APIResponseError as exc:
            return f"搜索失败：响应错误（{exc.message}）。"
        except ZaiError as exc:
            return f"搜索失败：{exc}。"
        except Exception as exc:  # noqa: BLE001
            return f"搜索失败：{type(exc).__name__}: {exc}。"

        result = response.search_result
        if not result:
            return "搜索无结果。"

        # 实际返回 list[SearchResultResp]，逐条格式化为可读文本。
        parts = []
        for item in result:
            title = item.title or "(无标题)"
            content = (item.content or "").strip()
            meta = "、".join(x for x in (item.media, item.publish_date) if x)
            refer = f"[{item.refer}] " if item.refer else ""
            link = f"\n链接：{item.link}" if item.link else ""
            head = f"{refer}{title}" + (f"（{meta}）" if meta else "")
            parts.append(f"{head}\n{content}{link}")
        return "\n\n".join(parts)

    return web_search_tool
