"""The git plugin."""

from datetime import datetime
from pathlib import Path
from typing import ClassVar

from ...api import (
    API_VERSION,
    ActionHandler,
    Asker,
    Changed,
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
    KindSpec,
    LoadRequest,
    Migration,
    Panel,
    PanelsRequest,
    Plugin,
    Ref,
    StoreRequest,
    attrs,
)
from ...api.shell import CommandError, git_common_dir
from . import actions, changes, client, events, watch
from .config import GitConfig
from .kinds import BranchAttrs, RepoAttrs
from .models import LocalSnapshot
from .panels import Builder, branch_ref, delete_iced_action, restore_iced_action
from .repository import MIGRATIONS, LocalRepository
from .setup import TEMPLATE, questions

REPO = "git.repo"
BRANCH = "git.branch"
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
    title = "Git"
    init_template = TEMPLATE
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
    notification_kinds: ClassVar[dict[str, str]] = events.KINDS

    def __init__(self, watching: bool = True) -> None:
        self._watching = watching
        """Whether `changed` watches the repos (see `watch`), or only polls them."""
        self._watcher: watch.Watcher | None = None
        self._ids: dict[Path, str] = {}
        """Each watched repo's id, the common git directory `changes` and `collect` name it
        by, by its resolved path, as the watcher has it."""
        self._clones_warned: set[tuple[str, ...]] = set()
        """The clones of one GitHub repo already warned of, so each is warned of once a run."""

    def init_suggested(self) -> bool:
        """Always: init guesses its repo from the checkout it runs in."""
        return True

    def init_questions(self, ask: Asker, setup: InitSetup) -> dict[str, object]:
        return questions(ask, setup)

    def collect(self, req: CollectRequest[GitConfig]) -> LocalSnapshot:
        """Every repo; or, after `changed` saw only some move, just those, keeping the rest
        as stored, unless a full collect is due (`refresh_seconds` since the last), so
        frequent changes can't put it off. What moved is taken as the collect starts: one that
        fails waits for the next refresh rather than asking again at once, and isn't counted
        as a full collect. What was read before stands in for a repo git can't read now."""
        ctx = req.ctx
        moved = set(ctx.kv.get_json(MOVED, list[str]) or [])
        ctx.kv.delete(MOVED)
        last_full = ctx.kv.get(LAST_FULL)
        full_due = (
            last_full is None
            or (ctx.now - datetime.fromisoformat(last_full)).total_seconds()
            >= ctx.config.refresh_seconds
        )
        previous = self.load(LoadRequest(ctx))
        partial = bool(moved) and not full_due and previous is not None
        found = client.collect(
            ctx.config.repos,
            ctx.now,
            ctx.config.commit_history_days,
            ctx.config.ignored_worktree_globs,
            only=moved if partial else None,
            previous=previous,
            github_hosts=ctx.config.github_hosts,
        )
        if not partial:
            ctx.kv.set(LAST_FULL, ctx.now.isoformat())
        by_name: dict[str, tuple[str, list[str]]] = {}  # GitHub's names ignore case
        for repo in found.repos:
            if repo.github_repo:
                by_name.setdefault(repo.github_repo.casefold(), (repo.github_repo, []))[1].append(
                    repo.dir
                )
        for name, dirs in by_name.values():
            if len(dirs) > 1 and tuple(dirs) not in self._clones_warned:
                self._clones_warned.add(tuple(dirs))
                ctx.log.warning(
                    "%s are each a clone of %s: only the first's branches meet its PRs; make the"
                    " others worktrees of it (git worktree add)",
                    ", ".join(dirs),
                    name,
                )
        return found

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
        their edits every `status_seconds` by `git status`. Which repos moved is kept for
        `collect`, to read just those, and asks for a refresh until a collect takes them."""
        ctx = req.ctx
        watcher = self._watcher_for(ctx)
        moved_now, edited_now = watcher.take() if watcher else (set[Path](), set[Path]())
        watched: set[str] = (
            {self._ids[c] for c in watcher.repos if c in self._ids} if watcher else set()
        )
        moved = {self._ids[c] for c in moved_now if c in self._ids}

        before = ctx.kv.get_json(FINGERPRINTS, dict[str, str])
        polled = [repo for repo in ctx.config.repos if str(git_common_dir(repo)) not in watched]
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
        # Pending until `collect` takes them: what's seen in the check core makes right after
        # a refresh (whose answer it ignores) still asks for a refresh at the next one.
        return Changed(
            bool(pending) or (before is not None and set(before) != set(now)),
            "a repo moved or was edited",
        )

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
                    found.add(str(git_common_dir(Path(repo.dir))))
        return found

    def load(self, req: LoadRequest[GitConfig]) -> LocalSnapshot | None:
        ctx = req.ctx
        return LocalRepository(ctx.db).load()

    def entities(self, req: EntitiesRequest[GitConfig, LocalSnapshot]) -> list[Entity]:
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
            "pull_branch": actions.pull_branch,
            "wip_commit": actions.wip_commit,
        }
