"""Server 轮次编排：changes。"""


import logging
from collections.abc import Mapping
from typing import Any

from ai_coding_harness.infra import repo_cache
from ai_coding_harness.infra.repo_cache import RepoSpec, RepoViewError
from ai_coding_harness.session.manager import (
    Session,
)

logger = logging.getLogger(__name__)

from .common import (
    _CHANGES_MAX_FILES,
)


class TurnChangesMixin:
    """changes 相关方法（由 HarnessService 组合）。"""

    async def _changes_for_round(
        self, session: Session, pr_refs: Mapping[str, str]
    ) -> dict[str, Any] | None:
        """这一轮的 PR 改了哪些文件 —— **在本地那份代码缓存里算**（2026-10-14 用户口径）。

        不碰沙箱、不查平台接口：``base...head`` 两支都由 PR 的入参给（``_pr_refs``），
        裸仓里有对象就能算。缓存里还没这个仓库（从没打开过代码面板）时返回 ``None``：
        一次 clone 可能很大，不该在一轮对话的收尾里顺手做掉 —— 界面上那行摘要就不出现，
        与「这一轮没改代码」看起来一样（这也是用户口径：展示的前提是写完并提了 PR）。

        本地已有缓存但缺那两支分支（coder 刚推上去的，本地还没 fetch）时先 ``sync()`` 一次
        再算 —— 这一杆也顺带把代码面板的缓存刷新了。
        """
        base = pr_refs.get("base", "")
        head = pr_refs.get("head", "")
        if not base or not head:
            return None
        spec = RepoSpec.from_session(session)
        try:
            if not repo_cache.is_cloned(self._settings, spec):
                return None
            try:
                files = await repo_cache.compare(self._settings, spec, base=base, head=head)
            except RepoViewError:
                await repo_cache.sync(self._settings, spec)
                files = await repo_cache.compare(self._settings, spec, base=base, head=head)
        except Exception:
            logger.exception("算这一轮的改动失败（本轮照常收尾）")
            return None
        payload = {
            "base": base,
            "head": head,
            "files": [
                {"path": item.path, "added": item.added, "deleted": item.deleted, "status": item.status}
                for item in files[:_CHANGES_MAX_FILES]
            ],
        }
        if len(files) > _CHANGES_MAX_FILES:
            payload["total"] = len(files)
        return payload
