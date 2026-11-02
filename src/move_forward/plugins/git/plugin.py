"""The git plugin."""

import json
from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel

from ...asking import Asker, InitSetup
from ...graph import Entity, Graph, Ref, attrs
from ...page import Action, Item, Panel
from ...plugin import ActionHandler, Context, Plugin
from . import actions, changes, client
from .config import GitConfig
from .kinds import BranchAttrs, RepoAttrs
from .models import LocalSnapshot
from .panels import Builder, branch_ref, delete_iced_action, restore_iced_action
from .repository import MIGRATIONS, LocalRepository
from .setup import TEMPLATE, questions

REPO = "repo"
BRANCH = "branch"
FINGERPRINTS = "refs_fingerprints"
"""The kv key of the last `changes.fingerprints` of the repos."""
MOVED = "repos_moved"
"""The kv key of the repos (common git directories) `changed` saw move, for `collect`."""
LAST_FULL = "last_full_collect"
"""The kv key of when every repo was last read."""


class GitPlugin(Plugin[GitConfig, LocalSnapshot]):
    name = "git"
    title = "Local"
    init_template = TEMPLATE

    def init_questions(self, ask: Asker, setup: InitSetup) -> dict[str, object]:
        return questions(ask, setup)

    config_model = GitConfig
    snapshot_model = LocalSnapshot
    migrations: ClassVar[list[str]] = MIGRATIONS
    kinds: ClassVar[dict[str, type[BaseModel]]] = {REPO: RepoAttrs, BRANCH: BranchAttrs}

    def collect(self, ctx: Context[GitConfig]) -> LocalSnapshot:
        """Every repo; or, after `changed` saw only some move, just those, keeping the rest
        as stored, unless a full collect is due (`refresh_seconds` since the last), so
        frequent changes can't put it off."""
        moved = set(json.loads(ctx.kv.get(MOVED) or "[]"))
        ctx.kv.set(MOVED, "[]")
        last_full = ctx.kv.get(LAST_FULL)
        full_due = (
            last_full is None
            or (ctx.now - datetime.fromisoformat(last_full)).total_seconds()
            >= ctx.config.refresh_seconds
        )
        previous = None if full_due or not moved else self.load(ctx)
        if previous is None:
            ctx.kv.set(LAST_FULL, ctx.now.isoformat())
        return client.collect(
            ctx.config.repos,
            ctx.now,
            ctx.config.commit_history_days,
            ctx.config.ignored_worktree_globs,
            only=moved if previous is not None else None,
            previous=previous,
            github_hosts=ctx.config.github_hosts,
        )

    def store(self, ctx: Context[GitConfig], snapshot: LocalSnapshot) -> None:
        LocalRepository(ctx.db).replace(snapshot)

    change_check_seconds: ClassVar[int | None] = 5

    def changed(self, ctx: Context[GitConfig], graph: Graph) -> bool:
        """Whether a repo's refs moved (see `changes`): reading a few files' times, cheap
        enough to do every few seconds. Which ones moved is kept for `collect`, to read just
        those."""
        now = changes.fingerprints(ctx.config.repos)
        stored = ctx.kv.get(FINGERPRINTS)
        before: dict[str, str] | None = json.loads(stored) if stored else None
        if now != before:
            ctx.kv.set(FINGERPRINTS, json.dumps(now))
        if before is None:
            return False
        if moved := changes.moved(before, now):
            pending = set(json.loads(ctx.kv.get(MOVED) or "[]")) | moved
            ctx.kv.set(MOVED, json.dumps(sorted(pending)))
        return bool(moved) or set(before) != set(now)

    def load(self, ctx: Context[GitConfig]) -> LocalSnapshot | None:
        return LocalRepository(ctx.db).load()

    def entities(self, ctx: Context[GitConfig], snapshot: LocalSnapshot | None) -> list[Entity]:
        """A `repo` per checkout on GitHub, and a `branch` per local branch."""
        entities: list[Entity] = []
        for repo in snapshot.repos if snapshot else []:
            repo_ref = Ref(kind=REPO, id=repo.github_repo) if repo.github_repo else None
            if repo_ref:
                entities.append(
                    Entity(
                        ref=repo_ref,
                        title=repo.github_repo or repo.dir,
                        attrs=attrs(
                            RepoAttrs(
                                dir=repo.dir,
                                default_branch=repo.default_branch,
                                default_refs=list(repo.default_refs),
                                my_dirs=list(repo.my_dirs),
                            )
                        ),
                    )
                )
            checked_out = {wt.branch: wt.path for wt in repo.worktrees if wt.branch}
            for branch in repo.branches:
                entities.append(
                    Entity(
                        ref=branch_ref(repo.dir, branch.name),
                        title=branch.name,
                        refs=[repo_ref] if repo_ref else [],
                        attrs=attrs(
                            BranchAttrs(
                                repo_dir=repo.dir,
                                name=branch.name,
                                sha=branch.sha,
                                ahead=branch.ahead,
                                behind=branch.behind,
                                gone=branch.gone,
                                checked_out_in=checked_out.get(branch.name),
                            )
                        ),
                    )
                )
        return entities

    def panels(
        self, ctx: Context[GitConfig], snapshot: LocalSnapshot | None, graph: Graph
    ) -> list[Panel]:
        return Builder(ctx.config, ctx.now, snapshot, graph).panels()

    def item_actions(self, ctx: Context[GitConfig], item: Item, graph: Graph) -> list[Action]:
        found = (delete_iced_action(item, graph), restore_iced_action(item, graph))
        return [action for action in found if action]

    def actions(self) -> dict[str, ActionHandler]:
        return {
            "delete_branch": actions.delete_branch,
            "delete_branches": actions.delete_branches,
            "delete_together": actions.delete_together,
            "restore_together": actions.restore_together,
            "push_branch": actions.push_branch,
            "wip_commit": actions.wip_commit,
        }
