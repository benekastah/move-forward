"""The git plugin."""

from datetime import datetime
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel

from ...api import (
    ActionHandler,
    Asker,
    ChangedRequest,
    CollectRequest,
    Context,
    EntitiesRequest,
    Entity,
    Event,
    GraphEventsRequest,
    InitSetup,
    ItemActionsRequest,
    ItemAdditions,
    LoadRequest,
    Panel,
    PanelsRequest,
    Plugin,
    Ref,
    StoreRequest,
    attrs,
)
from ...api.shell import CommandError
from . import actions, changes, client, events, watch
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
LAST_STATUS = "last_status_check"
"""The kv key of when `changed` last checked the worktrees' status."""


class GitPlugin(Plugin[GitConfig, LocalSnapshot]):
    name = "git"
    title = "Local"
    init_template = TEMPLATE
    config_model = GitConfig
    snapshot_model = LocalSnapshot
    migrations: ClassVar[list[str]] = MIGRATIONS
    kinds: ClassVar[dict[str, type[BaseModel]]] = {REPO: RepoAttrs, BRANCH: BranchAttrs}
    change_check_seconds: ClassVar[int | None] = 2
    notification_kinds: ClassVar[dict[str, str]] = events.KINDS

    def __init__(self, watching: bool = True) -> None:
        self._watching = watching
        """Whether `changed` watches the repos (see `watch`), or only polls them."""
        self._watcher: watch.Watcher | None = None
        self._ids: dict[Path, str] = {}
        """Each watched repo's id, the common git directory `changes` and `collect` name it
        by, by its resolved path, as the watcher has it."""

    def init_questions(self, ask: Asker, setup: InitSetup) -> dict[str, object]:
        return questions(ask, setup)

    def collect(self, req: CollectRequest[GitConfig]) -> LocalSnapshot:
        """Every repo; or, after `changed` saw only some move, just those, keeping the rest
        as stored, unless a full collect is due (`refresh_seconds` since the last), so
        frequent changes can't put it off."""
        ctx = req.ctx
        moved = set(ctx.kv.get_json(MOVED, list[str]) or [])
        ctx.kv.delete(MOVED)
        last_full = ctx.kv.get(LAST_FULL)
        full_due = (
            last_full is None
            or (ctx.now - datetime.fromisoformat(last_full)).total_seconds()
            >= ctx.config.refresh_seconds
        )
        previous = None if full_due or not moved else self.load(LoadRequest(ctx))
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

    def store(self, req: StoreRequest[GitConfig, LocalSnapshot]) -> None:
        """Keep the snapshot, and watch the worktrees it found (once `changed` has started
        watching: only the server asks it)."""
        ctx, snapshot = req.ctx, req.snapshot
        LocalRepository(ctx.db).replace(snapshot)
        if self._watcher is not None:
            self._watch(snapshot)

    def _watcher_for(self, ctx: Context[GitConfig]) -> watch.Watcher | None:
        """The watcher, started on the first call; None when not watching, or when it can't
        start."""
        if self._watching and self._watcher is None:
            try:
                self._watcher = watch.Watcher()
            except Exception as e:  # noqa: BLE001 - polling still works without it
                ctx.log.warning("can't watch the repos, so polling them: %s", e)
                self._watching = False
                return None
            if snapshot := self.load(LoadRequest(ctx)):
                self._watch(snapshot)
        return self._watcher

    def stop_watching(self) -> None:
        """Stop the watcher, if it started; `changed` starts it again."""
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None

    def _watch(self, snapshot: LocalSnapshot) -> None:
        assert self._watcher is not None
        worktrees: dict[Path, Path] = {}
        for repo in snapshot.repos:
            if (common := changes.common_dir(Path(repo.dir))) is None:
                continue
            self._ids[common.resolve()] = str(common)
            for wt in repo.worktrees:
                worktrees[Path(wt.path).resolve()] = common.resolve()
        self._watcher.sync(worktrees)

    def changed(self, req: ChangedRequest[GitConfig]) -> bool:
        """Whether a repo moved (a commit, checkout, branch, rebase, push or fetch), or a
        worktree's uncommitted edits changed. Watched repos say so as it happens (see
        `watch`); the rest are polled, their refs by their files' times (see `changes`) and
        their edits every `status_seconds` by `git status`. Which repos moved is kept for
        `collect`, to read just those, and asks for a refresh until it has."""
        ctx = req.ctx
        watcher = self._watcher_for(ctx)
        moved_now, edited_now = watcher.take() if watcher else (set[Path](), set[Path]())
        watched: set[str] = (
            {self._ids[c] for c in watcher.repos if c in self._ids} if watcher else set()
        )
        moved = {self._ids[c] for c in moved_now if c in self._ids}

        before = ctx.kv.get_json(FINGERPRINTS, dict[str, str])
        polled = [repo for repo in ctx.config.repos if str(changes.common_dir(repo)) not in watched]
        now = changes.fingerprints(polled)
        now.update({k: v for k, v in (before or {}).items() if k in watched})
        if now != before:
            ctx.kv.set_json(FINGERPRINTS, now)
        if before is not None:
            moved |= changes.moved(before, now)
        moved |= self._edited(ctx, edited_now, watcher.worktrees if watcher else set())
        pending = set(ctx.kv.get_json(MOVED, list[str]) or [])
        if moved - pending:
            pending |= moved
            ctx.kv.set_json(MOVED, sorted(pending))
        # Pending until `collect` reads them: what's seen in the check core makes right after
        # a refresh (whose answer it ignores) still asks for a refresh at the next one.
        return bool(pending) or (before is not None and set(before) != set(now))

    def _edited(self, ctx: Context[GitConfig], edited: set[Path], watched: set[Path]) -> set[str]:
        """The repos (common git directories) with a worktree whose uncommitted edits differ
        from the last refresh's, asking `git status` of the worktrees the watcher saw edited,
        and of those it doesn't watch once `status_seconds` have passed since the last look."""
        seconds = ctx.config.status_seconds
        last = ctx.kv.get(LAST_STATUS)
        poll = bool(seconds) and (
            last is None or (ctx.now - datetime.fromisoformat(last)).total_seconds() >= seconds
        )
        if poll:
            ctx.kv.set(LAST_STATUS, ctx.now.isoformat())
        if not poll and not edited:
            return set()
        found: set[str] = set()
        for repo in snapshot.repos if (snapshot := self.load(LoadRequest(ctx))) else []:
            for wt in repo.worktrees:
                path = Path(wt.path)
                resolved = path.resolve()
                if resolved not in edited and (not poll or resolved in watched):
                    continue
                try:
                    status = client.worktree_status(path)
                except CommandError:  # gone since; the next read finds out
                    continue
                if status != (wt.modified, wt.untracked):
                    found.add(str(changes.common_dir(Path(repo.dir))))
        return found

    def load(self, req: LoadRequest[GitConfig]) -> LocalSnapshot | None:
        ctx = req.ctx
        return LocalRepository(ctx.db).load()

    def entities(self, req: EntitiesRequest[GitConfig, LocalSnapshot]) -> list[Entity]:
        """A `repo` per checkout on GitHub, and a `branch` per local branch."""
        snapshot = req.snapshot
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

    def panels(self, req: PanelsRequest[GitConfig, LocalSnapshot]) -> list[Panel]:
        ctx, snapshot, graph = req.ctx, req.snapshot, req.graph
        return Builder(ctx.config, ctx.now, snapshot, graph).panels()

    def graph_events(self, req: GraphEventsRequest[GitConfig, LocalSnapshot]) -> list[Event]:
        """`branch_deletable`: a branch Cleanup lists now and didn't before this refresh."""
        if req.previous_snapshot is None:
            return []
        config, now = req.ctx.config, req.ctx.now
        before = Builder(config, now, req.previous_snapshot, req.previous_graph).cleanup()
        return events.newly_deletable(
            before, Builder(config, now, req.snapshot, req.graph).cleanup()
        )

    def item_actions(self, req: ItemActionsRequest[GitConfig]) -> ItemAdditions:
        item, graph = req.item, req.graph
        found = (delete_iced_action(item, graph), restore_iced_action(item, graph))
        return ItemAdditions(actions=[action for action in found if action])

    def actions(self) -> dict[str, ActionHandler]:
        return {
            "delete_branch": actions.delete_branch,
            "delete_branches": actions.delete_branches,
            "delete_together": actions.delete_together,
            "restore_together": actions.restore_together,
            "push_branch": actions.push_branch,
            "wip_commit": actions.wip_commit,
        }
