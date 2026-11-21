"""Hierarchical repository memory: a small index plus on-demand documents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from deepagents.backends.protocol import BackendProtocol

from ai_coding_harness.agent.middleware.memory_reconcile import strip_legacy
from ai_coding_harness.agent.middleware.memory_sections import (
    SECTION_ARCHITECTURE,
    SECTION_BASIC,
    SECTION_CATALOG,
    SECTION_CHANGES,
    SECTION_DECISIONS,
    SECTION_DIRECTORY,
    SECTION_GAPS,
    MemoryDoc,
    empty_memory,
    empty_sidecar,
    parse,
    render_memory,
    replace_section,
)

DOC_KINDS: dict[str, str] = {
    "architecture": "architecture",
    "domain": "domains",
    "feature": "features",
    "contract": "contracts",
    "operation": "operations",
    "decision": "decisions",
}

DEFAULT_DOCUMENTS: tuple[tuple[str, str, str], ...] = (
    ("architecture/directory.md", "语义目录、入口与模块职责", "directory,目录,模块"),
    ("architecture/overview.md", "整体架构与关键依赖", "architecture,系统结构"),
    ("architecture/modules.md", "模块职责与依赖方向", "modules,module,模块"),
    ("working/gaps.md", "已确认的功能缺口与未完成能力", "gaps,缺口,未完成"),
    ("working/risks.md", "安全、数据、并发和兼容性风险", "risks,风险,隐患"),
    ("working/decisions.md", "长期约束与技术决策", "decision,约束,ADR"),
    ("history/changes.md", "按 PR 记录的近期变化", "history,PR,变更"),
)

_CATALOG_LINE = re.compile(r"^\s*[-*+]\s+`(?P<path>[^`]+)`(?:\s*[—-]\s*(?P<summary>.*))?$")
_KEYWORDS = re.compile(r"(?:关键词|keywords)\s*[:：]\s*(?P<value>[^｜|]+)", re.IGNORECASE)
_SAFE_SLUG = re.compile(r"[^a-z0-9._-]+")


@dataclass(frozen=True)
class MemoryDocument:
    path: str
    summary: str
    keywords: tuple[str, ...] = ()


async def read_text(backend: BackendProtocol, path: str) -> str | None:
    stored = await backend.aread(path)
    return (getattr(stored, "file_data", None) or {}).get("content")


async def write_text(backend: BackendProtocol, path: str, old: str | None, new: str) -> None:
    result = await backend.awrite(path, new) if old is None else await backend.aedit(path, old, new)
    error = getattr(result, "error", None)
    if error:
        raise RuntimeError(str(error))


def document_entries(index: str | None) -> tuple[MemoryDocument, ...]:
    """Read catalog entries from the injected index."""
    if not index:
        return ()
    doc = parse(index)
    out: list[MemoryDocument] = []
    seen: set[str] = set()
    for line in doc.body(SECTION_CATALOG):
        match = _CATALOG_LINE.match(line)
        if match is None:
            continue
        path = match.group("path").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        tail = (match.group("summary") or "").strip()
        keyword_match = _KEYWORDS.search(tail)
        keywords = (
            tuple(item.strip() for item in keyword_match.group("value").split(",") if item.strip())
            if keyword_match
            else ()
        )
        summary = _KEYWORDS.sub("", tail).rstrip(" ｜|").strip() or path
        out.append(MemoryDocument(path=path, summary=summary, keywords=keywords))
    return tuple(out)


def _catalog_line(path: str, summary: str, keywords: tuple[str, ...]) -> str:
    suffix = f" — {summary}" if summary else ""
    if keywords:
        suffix += f" ｜ 关键词：{', '.join(keywords)}"
    return f"- `{path}`{suffix}"


def upsert_catalog(
    index: str,
    *,
    path: str,
    summary: str,
    keywords: tuple[str, ...] = (),
) -> str:
    """Insert or replace one navigable path in the main index."""
    doc = parse(index)
    lines = [line for line in doc.body(SECTION_CATALOG) if line.strip()]
    replacement = _catalog_line(path, summary, keywords)
    for position, line in enumerate(lines):
        match = _CATALOG_LINE.match(line)
        if match is not None and match.group("path").strip() == path:
            lines[position] = replacement
            break
    else:
        lines.append(replacement)
    replace_section(doc, SECTION_CATALOG, "\n".join(lines))
    return render_memory(doc)


def empty_index_with_catalog(owner: str, repo: str) -> str:
    index = empty_memory(owner, repo)
    for path, summary, keywords in DEFAULT_DOCUMENTS:
        index = upsert_catalog(
            index,
            path=path,
            summary=summary,
            keywords=tuple(item.strip() for item in keywords.split(",")),
        )
    return index


async def create_default_documents(
    backend: BackendProtocol,
    *,
    root_path: str,
    owner: str,
    repo: str,
) -> None:
    """Create readable skeletons for the standard detail documents."""
    skeletons = {
        "architecture/directory.md": (
            f"# 目录与模块：{owner}/{repo}\n\n"
            "## 目录导航\n\n## 入口与职责\n\n"
            "> 尚未生成语义目录；执行仓库引导分析后由主控补充。\n"
        ),
        "architecture/overview.md": (
            f"# 整体架构：{owner}/{repo}\n\n"
            "## 系统上下文\n\n## 容器与模块\n\n## 外部依赖\n\n"
            "> 尚未生成详细架构；执行仓库引导分析后由主控补充。\n"
        ),
        "architecture/modules.md": (
            f"# 模块依赖：{owner}/{repo}\n\n"
            "## 模块职责\n\n## 依赖方向\n\n"
            "> 尚未生成模块依赖图；执行仓库引导分析后由主控补充。\n"
        ),
        "working/gaps.md": empty_sidecar(f"当前缺口：{owner}/{repo}", SECTION_GAPS),
        "working/risks.md": (
            f"# 当前风险：{owner}/{repo}\n\n"
            "> 类型：risks\n\n"
            "## 风险登记\n\n"
            "> 只记录有代码证据的潜在不利后果；区分严重度、触发条件、影响与验证状态。\n"
        ),
        "working/decisions.md": empty_sidecar(f"长期约束：{owner}/{repo}", SECTION_DECISIONS),
        "history/changes.md": empty_sidecar(f"变更记录：{owner}/{repo}", SECTION_CHANGES),
    }
    for relative, content in skeletons.items():
        path = f"{root_path.rstrip('/')}/{relative}"
        if await read_text(backend, path) is None:
            await write_text(backend, path, None, content)


async def ensure_memory_pack(
    backend: BackendProtocol,
    *,
    index_path: str,
    owner: str,
    repo: str,
    now: str,
    persist: bool = True,
) -> tuple[str | None, bool]:
    """Normalize the index inside the repository memory directory.

    The previous ``<repo>.md`` key is intentionally unsupported; a missing
    ``<repo>/index.md`` means the repository has no current memory.
    """
    del now  # Reserved for future source-commit metadata.
    root_path = index_path.removesuffix("/index.md")
    old = await read_text(backend, index_path)
    if old is None:
        return None, False

    legacy = parse(strip_legacy(old))
    if any(
        any(section.key == key for section in legacy.sections)
        for key in (SECTION_DIRECTORY, SECTION_ARCHITECTURE, SECTION_CATALOG)
    ):
        return old, False

    basic = list(legacy.body(SECTION_BASIC))
    directory_lines: list[str] = []
    kept_basic: list[str] = []
    directory_field = re.compile(
        r"^\s*[-*+]\s*\*\*(?:目录结构|目录与模块)\*\*\s*[:：]\s*(?P<value>.*)$"
    )
    for line in basic:
        match = directory_field.match(line)
        if match is None:
            kept_basic.append(line)
        else:
            directory_lines.append(f"- **原目录说明**：{match.group('value').strip()}")

    index = MemoryDoc(preamble=list(legacy.preamble))
    index.set_body(SECTION_BASIC, kept_basic)
    index.set_body(
        SECTION_DIRECTORY,
        directory_lines
        or ["- 目录与模块将在下一次仓库分析时补全；详细内容见 `architecture/modules.md`。"],
    )
    index.set_body(
        SECTION_ARCHITECTURE,
        ["- 详细架构见 `architecture/overview.md`；主索引只保留压缩视图。"],
    )
    index.set_body(SECTION_CATALOG, [])
    rendered_index = render_memory(index)
    for path, summary, keywords in DEFAULT_DOCUMENTS:
        rendered_index = upsert_catalog(
            rendered_index,
            path=path,
            summary=summary,
            keywords=tuple(item.strip() for item in keywords.split(",")),
        )
    if not persist:
        return rendered_index, False

    for section, relative, title in (
        (SECTION_CHANGES, "history/changes.md", "变更记录"),
        (SECTION_GAPS, "working/gaps.md", "当前缺口"),
        (SECTION_DECISIONS, "working/decisions.md", "长期约束"),
    ):
        body = legacy.body(section)
        if not body:
            continue
        sidecar = parse(empty_sidecar(f"{title}：{owner}/{repo}", section))
        sidecar.set_body(section, body)
        rendered = render_memory(sidecar)
        side_path = f"{root_path.rstrip('/')}/{relative}"
        current = await read_text(backend, side_path)
        if current is None:
            await write_text(backend, side_path, None, rendered)

    if rendered_index != old:
        await write_text(backend, index_path, old, rendered_index)
    await create_default_documents(
        backend,
        root_path=root_path,
        owner=owner,
        repo=repo,
    )
    return rendered_index, rendered_index != old


def normalize_relative_path(path: str) -> str:
    target = PurePosixPath(str(path or "").strip().replace("\\", "/"))
    if not target.parts or target.is_absolute() or ".." in target.parts:
        raise ValueError("记忆文档路径必须是安全的相对路径")
    return target.as_posix()


def document_relative_path(kind: str, slug: str) -> str:
    folder = DOC_KINDS.get(kind)
    if folder is None:
        raise ValueError(f"kind 只能是 {' / '.join(DOC_KINDS)}")
    raw = str(slug or "").strip().lower()
    cleaned = _SAFE_SLUG.sub("-", raw).strip("-._")
    if not cleaned:
        raise ValueError("slug 不能为空，且只能包含字母、数字、点、下划线和连字符")
    return f"{folder}/{cleaned}.md"


def render_document(
    *,
    title: str,
    kind: str,
    summary: str,
    keywords: tuple[str, ...],
    content: str,
    now: str,
    verified_commit: str = "",
    source_paths: tuple[str, ...] = (),
    domain: str = "",
    related_domains: tuple[str, ...] = (),
) -> str:
    meta = [f"> 类型：{kind}　｜　最后更新：{now}"]
    if domain:
        meta.append(f"> 主要领域：{domain}")
    if related_domains:
        meta.append(f"> 关联领域：{', '.join(related_domains)}")
    if summary:
        meta.append(f"> 摘要：{summary}")
    if keywords:
        meta.append(f"> 关键词：{', '.join(keywords)}")
    if verified_commit:
        meta.append(f"> 验证提交：{verified_commit}")
    if source_paths:
        meta.append(f"> 代码依据：{', '.join(f'`{path}`' for path in source_paths)}")
    return "\n".join([f"# {title}", "", *meta, "", content.strip(), ""])


def document_path_for_access(root_path: str, requested: str) -> str:
    """Normalize a catalog path or logical ``/memories/...`` path to a backend path."""
    raw = str(requested or "").strip().replace("\\", "/")
    root = root_path.rstrip("/")
    if raw == root or raw.startswith(f"{root}/"):
        relative = raw[len(root) :].lstrip("/")
    else:
        relative = raw
    return f"{root}/{normalize_relative_path(relative)}"


def _upsert_heading_item(
    text: str,
    *,
    heading: str,
    path: str,
    summary: str,
) -> str:
    line = f"- `{path}`" + (f" — {summary}" if summary else "")
    lines = text.rstrip().splitlines()
    start = next(
        (index for index, current in enumerate(lines) if current.strip() == heading),
        None,
    )
    if start is None:
        lines.extend(["", heading, line])
        return "\n".join(lines).rstrip() + "\n"
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("## ")
        ),
        len(lines),
    )
    for index in range(start + 1, end):
        if f"`{path}`" in lines[index]:
            lines[index] = line
            break
    else:
        insert_at = end
        while insert_at > start + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines.insert(insert_at, line)
    return "\n".join(lines).rstrip() + "\n"


def update_domain_feature_index(
    text: str,
    *,
    feature_path: str,
    summary: str,
) -> str:
    return _upsert_heading_item(
        text,
        heading="## 功能索引",
        path=feature_path,
        summary=summary,
    )


def update_domain_related_index(
    text: str,
    *,
    feature_path: str,
    summary: str,
) -> str:
    return _upsert_heading_item(
        text,
        heading="## 关联功能",
        path=feature_path,
        summary=summary,
    )


__all__ = [
    "DOC_KINDS",
    "MemoryDocument",
    "create_default_documents",
    "document_entries",
    "document_path_for_access",
    "document_relative_path",
    "empty_index_with_catalog",
    "ensure_memory_pack",
    "normalize_relative_path",
    "read_text",
    "render_document",
    "update_domain_feature_index",
    "update_domain_related_index",
    "upsert_catalog",
    "write_text",
]
