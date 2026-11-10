"""The git plugin."""

from datetime import datetime
from pathlib import Path
from typing import ClassVar

from pydantic import TypeAdapter

from ...api import (
    API_VERSION,
    ActionHandler,
    Asker,
    Changed,
    ChangedRequest,
    CollectRequest,
    Context,
    ContextRequest,
    Entity,
    GraphRequest,
    InitSetup,
    ItemAdditions,
    ItemAdditionsFor,
    ItemRequest,
    KindSpec,
    Migration,
    Panel,
    PanelsRequest,
    Plugin,
    Ref,
    SnapshotRequest,
    attrs,
)
from ...api.shell import CommandError, git_common_dir
from . import actions, changes, client, watch
from .config import GitConfig
from .kinds import BranchAttrs, RepoAttrs
from .models import LocalSnapshot
from .panels import (
    NOTIFICATION_KINDS,
    Builder,
    branch_ref,
    delete_iced_action,
    restore_iced_action,
)
from .repository import MIGRATIONS, LocalRepository
from .setup import EXAMPLE, questions

REPO = "git.repo"
BRANCH = "git.branch"
LAST_STATUS = "last_status_check"
"""The kv key of when `changed` last checked the worktrees' status."""
_FINGERPRINTS = TypeAdapter(dict[str, str])
"""`changes.fingerprints`, as the fingerprint core keeps (`Changed.fingerprint`)."""
CLONES = "clones"
"""The problem (`Context.problem`) of the repos that are each a clone of one GitHub repo."""


class GitPlugin(Plugin[GitConfig, LocalSnapshot]):
    name = "git"
    title = "Git"
    init_example = EXAMPLE
    init_example_on = True
    config_model = GitConfig
    api_version = API_VERSION
    snapshot_model = LocalSnapshot
    locked_settings: ClassVar[frozenset[str]] = frozenset({"repos", "github_hosts"})
    """Where it runs git, and which remotes it takes for GitHub's (whose PRs it links)."""
    migrations: ClassVar[list[Migration]] = MIGRATIONS
    kinds: ClassVar[dict[str, KindSpec]] = {
        REPO: KindSpec(RepoAttrs),
        BRANCH: KindSpec(BranchAttrs, resolvable=True),
    }
    change_check_seconds: ClassVar[int | None] = 2
    notification_kinds: ClassVar[dict[str, str]] = NOTIFICATION_KINDS
    """`branch_deletable`: a branch newly in Cleanup (`Item.notify`)."""

    def __init__(self, watching: bool = True) -> None:
        self._watching = watching
        """Whether `changed` watches the repos (see `watch`), or only polls them."""
        self._watcher: watch.Watcher | None = None
        self._ids: dict[Path, str] = {}
        """Each watched repo's id, the common git directory `changes` and `collect` name it
        by, by its resolved path, as the watcher has it."""
        self._statuses: dict[Path, tuple[int, int]] = {}
        """Each worktree's (modified, untracked) counts as `_edited` last saw them, by its
        resolved path."""

    def init_suggested(self) -> bool:
        """Always: init guesses its repo from the checkout it runs in."""
        return True

    def init_questions(self, ask: Asker, setup: InitSetup) -> dict[str, object]:
        return questions(ask, setup)

    def collect(self, req: CollectRequest[GitConfig]) -> LocalSnapshot:
        """Every repo; or, after `changed` saw only some move (`req.subjects`, by common git
        directory), just those, keeping the rest as stored, unless core asks for a full
        collect (`refresh_seconds` since the last, so frequent changes can't put it off). What
        was read before stands in for a repo git can't read now. Repos that are each a clone of
        one GitHub repo are a problem (`CLONES`) until only one is left."""
        ctx = req.ctx
        previous = self.load(ContextRequest(ctx))
        partial = not req.full and previous is not None
        found = client.collect(
            ctx.config.repos,
            ctx.now,
            ctx.config.commit_history_days,
            ctx.config.ignored_worktree_globs,
            only=set(req.subjects) if partial else None,
            previous=previous,
            github_hosts=ctx.config.github_hosts,
        )
        by_name: dict[str, tuple[str, list[str]]] = {}  # GitHub's names ignore case
        for repo in found.repos:
            if repo.github_repo:
                by_name.setdefault(repo.github_repo.casefold(), (repo.github_repo, []))[1].append(
                    repo.dir
                )
        clones = [
            f"{', '.join(dirs)} are each a clone of {name}: only the first's branches meet its"
            " PRs; make the others worktrees of it (git worktree add)"
            for name, dirs in by_name.values()
            if len(dirs) > 1
        ]
        ctx.problem(CLONES, "; ".join(clones) or None)
        return found

    def store(self, req: SnapshotRequest[GitConfig, LocalSnapshot]) -> None:
        """Keep the snapshot, and watch the worktrees it found (once `changed` has started
        watching: only the server asks it). The last looks at uncommitted edits are forgotten:
        what's stored is now newer, and edits back as a look saw them may differ from it."""
        ctx, snapshot = req.ctx, req.snapshot
        LocalRepository(ctx.db).replace(snapshot)
        self._statuses.clear()
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
            if snapshot := self.load(ContextRequest(ctx)):
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
            if (common := git_common_dir(Path(repo.dir))) is None:
                continue
            self._ids[common.resolve()] = str(common)
            for wt in repo.worktrees:
                worktrees[Path(wt.path).resolve()] = common.resolve()
        self._watcher.sync(worktrees)

    def changed(self, req: ChangedRequest[GitConfig]) -> Changed:
        """Whether a repo moved (a commit, checkout, branch, rebase, push or fetch), or a
        worktree's uncommitted edits changed. Watched repos say so as it happens (see
        `watch`); the rest are polled, their refs by their files' times (see `changes`) and
        their edits every `status_seconds` by `git status`. The polled repos' refs are the
        fingerprint, by repo, which core keeps and gives back (`req.fingerprint`) to tell which
        moved; the repos that moved are the answer's subjects, which core keeps for `collect`,
        to read just those, and a repo found or gone asks for every repo."""
        ctx = req.ctx
        watcher = self._watcher_for(ctx)
        moved_now, edited_now = watcher.take() if watcher else (set[Path](), set[Path]())
        watched: set[str] = (
            {self._ids[c] for c in watcher.repos if c in self._ids} if watcher else set()
        )
        moved = {self._ids[c] for c in moved_now if c in self._ids}

        before = None if req.fingerprint is None else _FINGERPRINTS.validate_json(req.fingerprint)
        polled = [repo for repo in ctx.config.repos if str(git_common_dir(repo)) not in watched]
        now = changes.fingerprints(polled)
        now.update({k: v for k, v in (before or {}).items() if k in watched})
        if before is not None:
            moved |= changes.moved(before, now)
        moved |= self._edited(ctx, edited_now, watcher.worktrees if watcher else set())
        return Changed(
            fingerprint=_FINGERPRINTS.dump_json(now).decode(),
            subjects=frozenset(moved),
            everything=before is not None and set(before) != set(now),
            reason="a repo moved or was edited",
        )

    def _edited(self, ctx: Context[GitConfig], edited: set[Path], watched: set[Path]) -> set[str]:
        """The repos (common git directories) with a worktree whose uncommitted edits moved
        since the last look, asking `git status` of the worktrees the watcher saw edited, and
        of those it doesn't watch once `status_seconds` have passed since the last look. Edits
        that moved differ from the last look's and from what's stored: what's stored may be
        older than the last look (after a collect that failed), and a worktree whose edits are
        as stored needn't be read again."""
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
        for repo in snapshot.repos if (snapshot := self.load(ContextRequest(ctx))) else []:
            for wt in repo.worktrees:
                path = Path(wt.path)
                resolved = path.resolve()
                if resolved not in edited and (not poll or resolved in watched):
                    continue
                try:
                    status = client.worktree_status(path)
                except CommandError:  # gone since; the next read finds out
                    continue
                last = self._statuses.get(resolved)
                self._statuses[resolved] = status
                if status != (wt.modified, wt.untracked) and status != last:
                    found.add(str(git_common_dir(Path(repo.dir))))
        return found

    def load(self, req: ContextRequest[GitConfig]) -> LocalSnapshot | None:
        ctx = req.ctx
        return LocalRepository(ctx.db).load()

    def entities(self, req: SnapshotRequest[GitConfig, LocalSnapshot | None]) -> list[Entity]:
        """A `repo` per GitHub repo checked out (the first checkout of it, when two clones are,
        which `collect` warns of), and a `branch` per local branch."""
        snapshot = req.snapshot
        entities: list[Entity] = []
        published: dict[str, Ref] = {}  # by the name casefolded: GitHub's names ignore case
        for repo in snapshot.repos if snapshot else []:
            name = repo.github_repo
            # A later clone's branches point at the first's repo, the one published.
            repo_ref = published.get(name.casefold()) if name else None
            if name and repo_ref is None:
                repo_ref = published[name.casefold()] = Ref(kind=REPO, id=name)
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
                                commit_history_days=req.ctx.config.commit_history_days,
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
                                upstream=branch.upstream,
                                checked_out_in=checked_out.get(branch.name),
                            )
                        ),
                    )
                )
        return entities

    def panels(self, req: PanelsRequest[GitConfig, LocalSnapshot]) -> list[Panel]:
        ctx, snapshot, graph = req.ctx, req.snapshot, req.graph
        return Builder(ctx.config, ctx.now, snapshot, graph).panels()

    def item_actions(self, req: GraphRequest[GitConfig]) -> ItemAdditionsFor:
        """Delete or restore the branches an On ice item put on ice."""
        graph = req.graph

        def additions(each: ItemRequest) -> ItemAdditions:
            found = (delete_iced_action(each.item, graph), restore_iced_action(each.item, graph))
            return ItemAdditions(actions=[action for action in found if action])

        return additions

    def actions(self) -> dict[str, ActionHandler]:
        return {
            "delete_branch": actions.delete_branch,
            "delete_branches": actions.delete_branches,
            "delete_together": actions.delete_together,
            "restore_together": actions.restore_together,
            "push_branch": actions.push_branch,
            "pull_branch": actions.pull_branch,
            "wip_commit": actions.wip_commit,
        }
