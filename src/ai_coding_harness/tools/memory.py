"""仓库记忆：小型主索引、domain/feature 文档和按需读取。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from deepagents.backends.protocol import BackendProtocol
from langchain_core.tools import BaseTool, tool

from ai_coding_harness.agent.middleware.context_inject import now_stamp
from ai_coding_harness.agent.middleware.memory_reconcile import (
    restamp,
    same_except_stamp,
)
from ai_coding_harness.agent.middleware.memory_sections import (
    SECTION_ARCHITECTURE,
    SECTION_BASIC,
    SECTION_DIRECTORY,
    MemoryDoc,
    parse,
    patch_basic,
    render_memory,
    replace_section,
    section_sizes,
)
from ai_coding_harness.agent.middleware.repo_memory_pack import (
    create_default_documents,
    document_path_for_access,
    document_relative_path,
    empty_index_with_catalog,
    ensure_memory_pack,
    read_text,
    render_document,
    update_domain_feature_index,
    update_domain_related_index,
    upsert_catalog,
    write_text,
)
from ai_coding_harness.backends.composite import (
    memory_document_path,
    memory_path,
    memory_root,
)

MAX_INDEX = 6_000
MAX_BASIC = 1_500
MAX_DIRECTORY = 2_000
MAX_ARCHITECTURE = 2_000
MAX_DOCUMENT = 10_000
MAX_FIELD = 300

BASIC_FIELDS = ("项目地址", "技术栈", "启动方式", "测试 / 构建", "已知约定")
SAVE_KINDS = frozenset(
    {
        "profile",
        "directory",
        "architecture",
        "domain",
        "feature",
        "contract",
        "operation",
        "gaps",
        "risks",
        "decision",
    }
)
QA_KINDS = frozenset(
    {
        "profile",
        "directory",
        "architecture",
        "domain",
        "feature",
        "contract",
        "operation",
        "gaps",
        "risks",
    }
)

_PROFILE_FIELD = re.compile(r"^\s*[-*+]\s*\*\*(?P<label>[^*]+)\*\*\s*[:：]\s*(?P<value>.+?)\s*$")
_WORKING_DOCUMENTS = {
    "gaps": ("working/gaps.md", "当前缺口"),
    "risks": ("working/risks.md", "当前风险"),
}


@dataclass(frozen=True)
class MemoryContext:
    platform: str
    owner: str
    repo: str
    backend: BackendProtocol
    prefix: str = "/memories/"
    allowed_kinds: frozenset[str] | None = None
    persist_migration: bool = True


def _checked(value: object, *, limit: int, what: str) -> tuple[str, str | None]:
    text = str(value or "").strip()
    if len(text) > limit:
        return "", f"{what}超过 {limit} 字（{len(text)} 字），请精简后再提交。"
    return text, None


def _clean_list(value: list[str] | None, *, limit: int) -> tuple[str, ...]:
    return tuple(str(item).strip() for item in (value or []) if str(item).strip())[:limit]


def _mirror_excerpt(content: str, *, path: str, summary: str, limit: int) -> str:
    if len(content) <= limit:
        return content
    first = summary or "详细内容已拆分到文档。"
    return f"- {first}\n- 详情：`{path}`"


def build_memory_tools(ctx: MemoryContext) -> list[BaseTool]:
    """构建仓库记忆工具：读取一份文档，或按文档写入记忆。"""
    index_path = memory_path(ctx.platform, ctx.owner, ctx.repo, ctx.prefix)
    root_path = memory_root(ctx.platform, ctx.owner, ctx.repo, ctx.prefix)
    allowed = SAVE_KINDS if ctx.allowed_kinds is None else ctx.allowed_kinds

    async def ensure_index(*, persist: bool) -> tuple[str, str | None]:
        view, _changed = await ensure_memory_pack(
            ctx.backend,
            index_path=index_path,
            owner=ctx.owner,
            repo=ctx.repo,
            now=now_stamp(),
            persist=persist,
        )
        if not persist and view is not None:
            return view, view
        current = await read_text(ctx.backend, index_path)
        if current is not None:
            return current, current
        if not persist:
            return empty_index_with_catalog(ctx.owner, ctx.repo), None
        created = restamp(empty_index_with_catalog(ctx.owner, ctx.repo), now=now_stamp())
        await write_text(ctx.backend, index_path, None, created)
        await create_default_documents(
            ctx.backend,
            root_path=root_path,
            owner=ctx.owner,
            repo=ctx.repo,
        )
        return created, None

    def validate_index(doc: MemoryDoc, rendered: str) -> list[str]:
        sizes = section_sizes(doc)
        problems: list[str] = []
        if sizes[SECTION_BASIC] > MAX_BASIC:
            problems.append(f"仓库基本信息 {sizes[SECTION_BASIC]}/{MAX_BASIC}")
        if sizes[SECTION_DIRECTORY] > MAX_DIRECTORY:
            problems.append(f"目录与模块 {sizes[SECTION_DIRECTORY]}/{MAX_DIRECTORY}")
        if sizes[SECTION_ARCHITECTURE] > MAX_ARCHITECTURE:
            problems.append(f"架构概览 {sizes[SECTION_ARCHITECTURE]}/{MAX_ARCHITECTURE}")
        if len(rendered) > MAX_INDEX:
            problems.append(f"主索引 {len(rendered)}/{MAX_INDEX}")
        return problems

    @tool
    async def read_repo_memory(path: str) -> str:
        """按主索引“记忆目录”中的路径读取一份完整记忆文档。

        主索引已经常驻上下文，模型应直接选择相关路径，例如
        `domains/payment.md`、`features/payment-refund.md`、
        `architecture/overview.md`。本工具一次只读一份，不做搜索。
        """
        raw = str(path or "").strip()
        if not raw:
            return "path 不能为空。"
        try:
            if raw in {"index", "index.md"} or raw == index_path:
                text, _ = await ensure_index(persist=ctx.persist_migration)
                return text
            target = document_path_for_access(root_path, raw)
            text = await read_text(ctx.backend, target)
        except RuntimeError as exc:
            return f"读取仓库记忆失败：{exc}"
        return text if text is not None else f"仓库记忆文档不存在：`{raw}`"

    if not allowed:
        return [read_repo_memory]

    @tool
    async def save_repo_memory(
        kind: str,
        content: str,
        slug: str = "",
        title: str = "",
        summary: str = "",
        domain: str = "",
        related_domains: list[str] | None = None,
        keywords: list[str] | None = None,
        source_paths: list[str] | None = None,
        verified_commit: str = "",
    ) -> str:
        """写入或更新一份仓库语义记忆，不再按 section 或模块写入。

        `kind` 取值：
        - profile：更新主索引基本信息，content 使用 `- **技术栈**：...` 形式；
        - directory：更新语义目录，写入 `architecture/directory.md` 并同步主索引摘要；
        - architecture：写 `architecture/<slug>.md`；slug=overview 时同步压缩架构；
        - domain：写 `domains/<slug>.md`，描述领域边界和共享规则；
        - feature：写 `features/<slug>.md`；必须提供主要 domain，平台自动加入领域功能索引；
        - contract：写 API、事件、CLI 或数据契约；
        - operation：写构建、测试、部署或排障手册；
        - gaps：更新 `working/gaps.md`，只写已确认缺失或未完成的能力；
        - risks：更新 `working/risks.md`，写安全、数据完整性、并发、兼容性等潜在不利后果；
        - decision：写长期约束或 ADR，只在 coding 收尾使用。

        `content` 是完整 Markdown 正文，不写文件头、路径、更新时间或主索引目录。
        gaps / risks 至少提供一项证据：`source_paths` 写仓库相对的代码依据，
        `verified_commit` 写真实提交 SHA。
        """
        if kind not in allowed:
            return f"当前角色不能写 kind={kind}。允许的类型：" + " / ".join(sorted(allowed))
        body, problem = _checked(content, limit=MAX_DOCUMENT, what="content")
        if problem:
            return problem
        if not body:
            return "content 不能为空。"
        clean_summary, problem = _checked(summary, limit=180, what="summary")
        if problem:
            return problem
        try:
            index_text, _index_old = await ensure_index(persist=True)
        except RuntimeError as exc:
            return f"写入仓库记忆失败：{exc}"
        now = now_stamp()

        if kind == "profile":
            fields: dict[str, str] = {}
            for line in body.splitlines():
                match = _PROFILE_FIELD.match(line)
                if match is None:
                    continue
                label = match.group("label").strip().replace("测试构建", "测试 / 构建")
                if label not in BASIC_FIELDS:
                    return f"profile 不支持的字段「{label}」。只收：" + "、".join(BASIC_FIELDS)
                value, issue = _checked(
                    match.group("value"), limit=MAX_FIELD, what=f"字段「{label}」"
                )
                if issue:
                    return issue
                fields[label] = value
            if not fields:
                return "profile 需要至少一个 `- **字段**：值`。"
            doc = parse(index_text)
            patch_basic(doc, fields, allowed=BASIC_FIELDS)
            rendered = restamp(render_memory(doc), now=now)
            problems = validate_index(doc, rendered)
            if problems:
                return "仓库记忆未写入：超出上限（" + "、".join(problems) + "）。"
            current = await read_text(ctx.backend, index_path)
            if current is None or not same_except_stamp(current, rendered):
                try:
                    await write_text(ctx.backend, index_path, current, rendered)
                except RuntimeError as exc:
                    return f"写入仓库记忆失败：{exc}"
            return f"仓库基本信息已更新（{len(rendered)} 字）。"

        if kind in _WORKING_DOCUMENTS:
            relative, default_title = _WORKING_DOCUMENTS[kind]
            clean_title = title.strip() or default_title
        elif kind == "directory":
            relative = "architecture/directory.md"
            clean_title = title.strip() or "目录与模块"
        else:
            if not slug.strip():
                return f"{kind} 需要 slug。"
            try:
                relative = document_relative_path(kind, slug)
            except ValueError as exc:
                return str(exc)
            clean_title = title.strip() or slug.replace("-", " ").strip().title()
        clean_title, problem = _checked(clean_title, limit=120, what="title")
        if problem or not clean_title:
            return problem or "title 不能为空。"

        clean_keywords = _clean_list(keywords, limit=8)
        clean_sources = _clean_list(source_paths, limit=20)
        clean_domain, problem = _checked(domain, limit=80, what="domain")
        if problem:
            return problem
        clean_related = _clean_list(related_domains, limit=6)
        if kind == "feature" and not clean_domain:
            return "feature 必须提供主要 domain。"
        clean_commit = verified_commit.strip()
        if kind in _WORKING_DOCUMENTS and not clean_sources and not clean_commit:
            return f"{kind} 至少需要一项代码证据：source_paths 或 verified_commit。"
        rendered_doc = render_document(
            title=clean_title,
            kind=kind,
            summary=clean_summary,
            keywords=clean_keywords,
            content=body,
            now=now,
            verified_commit=clean_commit,
            source_paths=clean_sources,
            domain=clean_domain,
            related_domains=clean_related,
        )
        doc_path = memory_document_path(ctx.platform, ctx.owner, ctx.repo, relative, ctx.prefix)
        old_doc = await read_text(ctx.backend, doc_path)

        updated_index = upsert_catalog(
            index_text,
            path=relative,
            summary=clean_summary or clean_title,
            keywords=clean_keywords,
        )
        index_doc = parse(updated_index)
        if kind == "directory":
            replace_section(
                index_doc,
                SECTION_DIRECTORY,
                _mirror_excerpt(
                    body,
                    path=relative,
                    summary=clean_summary,
                    limit=MAX_DIRECTORY,
                ),
            )
        elif kind == "architecture" and slug == "overview":
            replace_section(
                index_doc,
                SECTION_ARCHITECTURE,
                _mirror_excerpt(
                    body,
                    path=relative,
                    summary=clean_summary,
                    limit=MAX_ARCHITECTURE,
                ),
            )
        updated_index = render_memory(index_doc)

        domain_writes: list[tuple[str, str | None, str]] = []
        if kind == "feature":
            domain_slugs = [clean_domain]
            domain_slugs.extend(clean_related)
            for domain_slug in dict.fromkeys(domain_slugs):
                try:
                    domain_relative = document_relative_path("domain", domain_slug)
                except ValueError as exc:
                    return str(exc)
                domain_path = memory_document_path(
                    ctx.platform,
                    ctx.owner,
                    ctx.repo,
                    domain_relative,
                    ctx.prefix,
                )
                domain_old = await read_text(ctx.backend, domain_path)
                domain_text = domain_old or (
                    f"# {domain_slug} 领域\n\n## 领域职责\n\n## 领域边界\n\n## 共享规则\n\n"
                )
                domain_text = (
                    update_domain_feature_index(
                        domain_text,
                        feature_path=relative,
                        summary=clean_summary or clean_title,
                    )
                    if domain_slug == clean_domain
                    else update_domain_related_index(
                        domain_text,
                        feature_path=relative,
                        summary=clean_summary or clean_title,
                    )
                )
                domain_writes.append((domain_path, domain_old, domain_text))
                updated_index = upsert_catalog(
                    updated_index,
                    path=domain_relative,
                    summary=f"{domain_slug} 业务领域",
                    keywords=(domain_slug,),
                )

        problems = validate_index(parse(updated_index), updated_index)
        if problems:
            return (
                "仓库记忆未写入：主索引超出上限（"
                + "、".join(problems)
                + "）。请把更多功能放进领域索引，主索引只列核心文档。"
            )
        try:
            if old_doc != rendered_doc:
                await write_text(ctx.backend, doc_path, old_doc, rendered_doc)
            for domain_path, domain_old, domain_text in domain_writes:
                if domain_old != domain_text:
                    await write_text(
                        ctx.backend,
                        domain_path,
                        domain_old,
                        domain_text,
                    )
            if updated_index != index_text:
                current_index = await read_text(ctx.backend, index_path)
                await write_text(ctx.backend, index_path, current_index, updated_index)
        except RuntimeError as exc:
            return f"写入仓库记忆失败：{exc}"
        return f"仓库记忆已更新：`{relative}`（{len(rendered_doc)} 字），主索引和领域索引已同步。"

    return [read_repo_memory, save_repo_memory]


__all__ = [
    "QA_KINDS",
    "SAVE_KINDS",
    "MemoryContext",
    "build_memory_tools",
]
