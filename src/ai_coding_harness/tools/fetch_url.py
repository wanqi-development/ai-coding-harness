"""Fetch URL tool — 拉取网页内容并转换为 Markdown。"""

from __future__ import annotations

import httpx
from bs4 import BeautifulSoup
from langchain_core.tools import BaseTool, tool
from markdownify import markdownify as html_to_md

# 内容截断阈值（字符），避免过长的页面撑爆上下文
MAX_CONTENT_LEN = 20_000
# 转 markdown 前剥离的无关标签
_STRIP_TAGS = ["script", "style", "nav", "header", "footer", "aside", "iframe", "noscript"]


def build_fetch_url_tool() -> BaseTool:
    """构建 fetch_url 工具（无需配置）。"""

    @tool
    def fetch_url(url: str) -> str:
        """从网页拉取内容并转换为 Markdown 文档。

        适用：用户提供了网页地址，需要读取该页面内容时。

        Args:
            url: 网页地址（http/https）。
        """
        # 1. 拉取（含网络/超时错误处理）
        try:
            with httpx.Client(timeout=20, follow_redirects=True) as client:
                resp = client.get(url)
        except httpx.TimeoutException:
            return "抓取失败：请求超时，请稍后重试。"
        except httpx.ConnectError:
            return "抓取失败：无法连接该地址，请检查 URL 是否正确。"
        except httpx.HTTPError as exc:
            return f"抓取失败：网络错误（{type(exc).__name__}）。"
        except Exception as exc:  # noqa: BLE001
            return f"抓取失败：{type(exc).__name__}: {exc}。"

        # 2. HTTP 状态与限流处理
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After", "未知")
            return f"抓取失败：请求被限流（429），建议 {retry_after} 秒后重试。"
        if resp.status_code == 403:
            return "抓取失败：访问被拒绝（403），可能需要登录或该站点禁止抓取。"
        if resp.status_code == 404:
            return "抓取失败：页面不存在（404）。"
        if resp.status_code >= 400:
            return f"抓取失败：HTTP {resp.status_code}。"

        # 3. 类型检查（仅处理 HTML/文本页面）
        content_type = resp.headers.get("Content-Type", "")
        if "html" not in content_type.lower() and "text" not in content_type.lower():
            return f"抓取失败：不支持的响应类型（{content_type}），仅支持 HTML/文本页面。"

        # 4. 剥离无关标签后转 markdown
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(_STRIP_TAGS):
            tag.decompose()
        md = html_to_md(str(soup))
        if not md or not md.strip():
            return "抓取结果为空：页面没有可转换的内容。"

        if len(md) > MAX_CONTENT_LEN:
            md = md[:MAX_CONTENT_LEN] + "\n\n[内容过长，已截断]"
        return md

    return fetch_url
