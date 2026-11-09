"""The GitHub plugin."""

import subprocess
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, ClassVar, cast
from urllib.parse import urlsplit

from ...api import (
    API_VERSION,
    ActionHandler,
    ActNowRequest,
    AfterCollectRequest,
    Asker,
    Changed,
    ChangedRequest,
    ChangeRule,
    CollectRequest,
    Context,
    EntitiesRequest,
    Entity,
    Event,
    EventsRequest,
    FetchRequest,
    Graph,
    Identity,
    IdentityRequest,
    ImportRequest,
    InitSetup,
    Item,
    KindSpec,
    LoadRequest,
    Migration,
    Panel,
    PanelsRequest,
    Plugin,
    PruneRequest,
    Ref,
    RelateRequest,
    Signals,
    StoreRequest,
    Subjects,
    SubjectsRequest,
    UrlRequest,
    attrs,
    collect_into,
)
from ...api.kinds import PERSON
from ...api.shell import CommandError
from . import actions, changes, client, derive, notifications, placing, pushes, relations
from .config import GitHubConfig
from .kinds import GitHubPerson, GitHubPullRequest
from .models import GitHubSnapshot, PullRequest
from .panels import PR, REQUEST_REVIEWER_TASK, REVIEW_TASK, Builder
from .people import REVIEWER_MAP_FILE, PeopleRepository, read_reviewer_map
from .prs import (
    changes_requested,
    last_review_activity,
    policy_failures,
    progress,
    review_blockers,
    reviewer_assignment_reasons,
)
from .setup import EXAMPLE, TEMPLATE, questions
from .snapshots import DerivedRepository, PrRepository
from .tables import MIGRATIONS

DERIVED = "derived"
"""`after_collect`'s part of the refresh, `github.derived`: team scores and reviewer
suggestions. Its status is recorded under that name; what it found goes to its tables."""
REPO = "git.repo"
NOTIFICATIONS_SEEN = "notifications_last_modified"
"""The kv key of the notifications' last `Last-Modified`, for `changed`."""
NOTIFICATIONS_WAIT = "notifications_poll_after"
"""The kv key of when GitHub lets `changed` poll the notifications again."""
BRANCHES_SEEN = "pr_branches_seen"
"""The kv key of where my open PRs' local branches were at the last check (`pushes.tips`)."""
CHANGED = "prs_changed"
"""The kv key of the PRs (`owner/name#number`) `changed` saw change, for `collect`; with
`EVERYTHING` among them when it saw a change it can't pin on a PR."""
EVERYTHING = "*"
LAST_FULL = "last_full_collect"
"""The kv key of when every search last ran."""


def _pr_id(changed: str) -> client.PrId:
    repo, _, number = changed.rpartition("#")
    return client.PrId(repo=repo, number=int(number))


def pr_id_of(host: str, url: str) -> client.PrId | None:
    """The PR a URL on `host` is, `https://<host>/<owner>/<name>/pull/<number>`; None for
    anything else."""
    parts = urlsplit(url)
    path = parts.path.strip("/").split("/")
    if parts.netloc != host or len(path) != 4 or path[2] != "pull" or not path[3].isdigit():
        return None
    return client.PrId(repo=f"{path[0]}/{path[1]}", number=int(path[3]))


def wants_reviewer(config: GitHubConfig, pr: PullRequest) -> bool:
    """Whether a reviewer may yet be picked for one of my PRs, where a reviewer field or Needs
    a reviewer can show suggestions: not in the merge queue, and a draft only for its primary
    reviewer."""
    return not pr.in_merge_queue and (not pr.is_draft or config.has_primary_reviewer(pr.repo))


def _team_slugs(ctx: Context[GitHubConfig]) -> list[str]:
    return [ctx.config.slug(team) for team in ctx.teams]


class GitHubPlugin(Plugin[GitHubConfig, GitHubSnapshot]):
    name = "github"
    title = "GitHub"
    init_template = TEMPLATE
    init_example = EXAMPLE
    init_example_on = True
    init_finds_teams = True
    config_model = GitHubConfig
    api_version = API_VERSION
    teams_field = "teams"
    locked_settings: ClassVar[frozenset[str]] = frozenset({"host"})
    """Its token, `gh`'s, goes to `host`."""
    migrations: ClassVar[list[Migration]] = MIGRATIONS
    kinds: ClassVar[dict[str, KindSpec]] = {
        PR: KindSpec(GitHubPullRequest, fetchable=True, resolvable=True),
        PERSON: KindSpec(GitHubPerson),
    }
    snapshot_model = GitHubSnapshot
    change_check_seconds: ClassVar[int | None] = notifications.DEFAULT_POLL_SECONDS
    watches: ClassVar[list[str]] = ["git"]
    notification_kinds: ClassVar[dict[str, str]] = changes.KINDS
    change_rules: ClassVar[Sequence[ChangeRule[Any]]] = changes.RULES

    def __init__(self) -> None:
        self._poll_failed: str | None = None
        """Why the last poll of the notifications failed, warned of once while it lasts."""

    def init_suggested(self) -> bool:
        """Always: init guesses its org from the checkout it runs in."""
        return True

    def init_questions(self, ask: Asker, setup: InitSetup) -> dict[str, object]:
        return questions(ask, setup)

    def collect(self, req: CollectRequest[GitHubConfig]) -> GitHubSnapshot:
        """Every search; or, after `changed` saw only some PRs change, just those PRs, placed
        in the stored lists as the searches would find them. Every search runs when a full
        collect is due (`refresh_seconds` since the last), so frequent changes can't put it
        off, when a change wasn't about a PR (a CI run), when more PRs changed than a search
        page holds (a request for them all would time out), and when fetching them fails."""
        ctx = req.ctx
        config = ctx.config
        changed = self._take_changed(ctx)
        everything = EVERYTHING in changed or len(changed) > client.PAGE_SIZE
        previous = None if everything or self._full_due(ctx) else self.load(LoadRequest(ctx))
        if previous is not None and changed:
            ids = [_pr_id(pr) for pr in sorted(changed)]
            try:
                fetched = client.fetch_prs(config.host, ids)
            except CommandError as e:
                ctx.log.warning("fetching the PRs that changed failed, so fetching all: %s", e)
            else:
                placed = placing.place(previous, fetched, _team_slugs(ctx))
                memberships = dict(placed.team_memberships)
                for pr in placed.mine:
                    owner = pr.repo.partition("/")[0]
                    for team in pr.requested_teams:
                        key = f"{owner}/{team}"
                        policy = config.team_reviewer_assignment.get(
                            team, config.default_team_reviewer_assignment
                        )
                        if policy != "individual" or key in memberships:
                            continue
                        try:
                            memberships[key] = sorted(
                                set(client.team_members(config.host, owner, team))
                            )
                        except CommandError as e:
                            ctx.log.warning("cannot verify members of %s: %s", key, e)
                return placed.model_copy(
                    update={
                        "team_memberships": memberships,
                        "team_members": client.my_team_members(
                            memberships, config.org, _team_slugs(ctx)
                        ),
                    }
                )
        ctx.kv.set(LAST_FULL, ctx.now.isoformat())
        return client.collect(
            config.host,
            config.org,
            _team_slugs(ctx),
            config.history_days,
            ctx.now,
            config.search_scope,
            lambda team: (
                config.team_reviewer_assignment.get(team, config.default_team_reviewer_assignment)
                == "individual"
            ),
        )

    def _full_due(self, ctx: Context[GitHubConfig]) -> bool:
        last = ctx.kv.get(LAST_FULL)
        return (
            last is None
            or (ctx.now - datetime.fromisoformat(last)).total_seconds()
            >= ctx.config.refresh_seconds
        )

    def _take_changed(self, ctx: Context[GitHubConfig]) -> set[str]:
        changed = set(ctx.kv.get_json(CHANGED, list[str]) or [])
        ctx.kv.delete(CHANGED)
        return changed

    def _note_changed(self, ctx: Context[GitHubConfig], prs: set[str]) -> set[str]:
        """Add `prs` to those pending for `collect`; all that are pending."""
        pending = set(ctx.kv.get_json(CHANGED, list[str]) or [])
        if prs - pending:
            pending |= prs
            ctx.kv.set_json(CHANGED, sorted(pending))
        return pending

    def store(self, req: StoreRequest[GitHubConfig, GitHubSnapshot]) -> None:
        ctx, snapshot = req.ctx, req.snapshot
        PrRepository(ctx.db).replace(snapshot)

    def changed(self, req: ChangedRequest[GitHubConfig]) -> Changed:
        """Whether a branch of one of my open PRs moved (see `pushes`; asked right after each
        refresh of git, which it watches), or I have a notification about something my
        searches cover (`search_scope`), polled as often as GitHub allows. Which PRs changed is
        kept for `collect`, to fetch just those, and asks for a refresh until it has."""
        ctx, graph = req.ctx, req.graph
        # Polled first: anything it raises leaves the branches' tips to be compared next time.
        notified = self._notified(ctx)
        changed = self._branches_moved(ctx, graph) | notified
        # Pending until `collect` fetches them: what's seen in the check core makes right
        # after a refresh (whose answer it ignores) still asks for a refresh at the next one.
        pending = self._note_changed(ctx, changed)
        if EVERYTHING in pending:
            return Changed(True, "every search")
        return Changed(bool(pending), f"{len(pending)} PR(s) to fetch")

    def _branches_moved(self, ctx: Context[GitHubConfig], graph: Graph) -> set[str]:
        now = pushes.tips(graph, f"{self.name}.{PR}")
        before = ctx.kv.get_json(BRANCHES_SEEN, object)
        if now != before:
            ctx.kv.set_json(BRANCHES_SEEN, now)
        if not isinstance(before, dict):  # the first look, or kept in an older form
            return set()
        return pushes.moved(cast(dict[str, str], before), now)

    def _notified(self, ctx: Context[GitHubConfig]) -> set[str]:
        """The PRs I have a notification about where my searches look (a review requested, a
        comment, a review), newer than the last look. A CI run names only its repo, so it stands
        for my open PRs there; what no search shows (an issue, a release, an alert) for nothing;
        and anything else for `EVERYTHING`. A "not modified" answer is cheap, and GitHub's poll
        interval is kept to. A poll that fails finds nothing, warning once while the same reason
        lasts, and is tried again after the default interval."""
        wait = ctx.kv.get(NOTIFICATIONS_WAIT)
        if wait and ctx.now < datetime.fromisoformat(wait):
            return set()
        seen = ctx.kv.get(NOTIFICATIONS_SEEN)
        try:
            found = notifications.fetch(ctx.config.host, seen)
        except (CommandError, subprocess.SubprocessError, OSError, KeyError, ValueError) as e:
            # A token that can't read notifications (a fine-grained one can't), `gh` hanging
            # or a blip: the branch moves found with it still count.
            if self._poll_failed != str(e):
                ctx.log.warning("can't read GitHub notifications: %s", e)
                self._poll_failed = str(e)
            retry = ctx.now + timedelta(seconds=notifications.DEFAULT_POLL_SECONDS)
            ctx.kv.set(NOTIFICATIONS_WAIT, retry.isoformat())
            return set()
        self._poll_failed = None
        if found is None:
            return set()
        ctx.kv.set(NOTIFICATIONS_SEEN, found.last_modified)
        ctx.kv.set(
            NOTIFICATIONS_WAIT, (ctx.now + timedelta(seconds=found.poll_seconds)).isoformat()
        )
        if seen is None:
            return set()
        prs: set[str] = set()
        ci_repos: set[str] = set()
        org = ctx.config.org if ctx.config.search_scope == "org" else None
        for n in found.new_in(org, seen):
            if n.pr is not None:
                prs.add(f"{n.repo}#{n.pr}")
            elif n.kind == notifications.CHECK_SUITE:
                ci_repos.add(n.repo.lower())
            elif n.kind not in notifications.UNRELATED:
                prs.add(EVERYTHING)
        if ci_repos:
            if (snapshot := self.load(LoadRequest(ctx))) is None:
                return prs | {EVERYTHING}
            prs |= {f"{pr.repo}#{pr.number}" for pr in snapshot.mine if pr.repo.lower() in ci_repos}
        return prs

    def events(self, req: EventsRequest[GitHubConfig, GitHubSnapshot]) -> list[Event]:
        ctx, before, after = req.ctx, req.before, req.after
        return changes.changes(before, after, ctx.config.ignored_reviewers)

    def load(self, req: LoadRequest[GitHubConfig]) -> GitHubSnapshot | None:
        ctx = req.ctx
        return PrRepository(ctx.db).load()

    def identity(self, req: IdentityRequest[GitHubConfig, GitHubSnapshot]) -> Identity | None:
        """My GitHub login, as the last refresh found it."""
        snapshot = req.snapshot
        return (
            Identity(tool=self.title, login=snapshot.login) if snapshot and snapshot.login else None
        )

    def _email_domains(self, ctx: Context[GitHubConfig]) -> list[str]:
        if ctx.config.email_domains:
            return ctx.config.email_domains
        return [ctx.email.rsplit("@", 1)[1]] if "@" in ctx.email else []

    def _lookup_emails(self, ctx: Context[GitHubConfig], gh: GitHubSnapshot) -> None:
        """Cache the commit email of every requested reviewer we don't know yet."""
        domains = self._email_domains(ctx)
        if not domains:
            return
        people = PeopleRepository(ctx.db)
        known = people.emails()
        for pr in gh.mine:
            for login in pr.requested_users:
                if login in known:
                    continue
                try:
                    email = client.commit_email(ctx.config.host, pr.repo, login, domains)
                except Exception as e:  # noqa: BLE001
                    ctx.log.warning("email lookup for %s failed: %s", login, e)
                    continue
                people.save_looked_up(login, email, ctx.now)
                known[login] = email

    def after_collect(self, req: AfterCollectRequest[GitHubConfig]) -> None:
        """Look up reviewers' emails, then score team PRs and suggest reviewers from the
        local checkouts the graph's `git.repo` entities describe."""
        ctx, graph = req.ctx, req.graph
        gh = self.load(LoadRequest(ctx))
        if gh is None:
            return
        self._lookup_emails(ctx, gh)
        checkouts = [
            derive.Checkout.model_validate({"github_repo": repo.ref.id, **repo.attrs})
            for repo in graph.of_kind(REPO)
        ]
        collect_into(
            ctx.db,
            ctx.part(DERIVED),
            ctx.now,
            lambda: derive.collect(
                gh,
                checkouts,
                PeopleRepository(ctx.db).emails(),
                ctx.config.org,
                _team_slugs(ctx),
                ctx.config.required_owner_marker,
                wants_reviewer=lambda pr: wants_reviewer(ctx.config, pr),
            ),
            DerivedRepository(ctx.db).replace,
        )

    def _pr_entity(
        self,
        ctx: Context[GitHubConfig],
        pr: PullRequest,
        me: str,
        state: str,
        mine: bool,
        team_names: dict[str, str],
        memberships: dict[str, list[str]],
    ) -> Entity:
        fields: dict[str, object] = {
            "repo": pr.repo,
            "number": pr.number,
            "head": pr.head,
            "head_oid": pr.head_oid,
            "base": pr.base,
            "author": pr.author,
            "draft": pr.is_draft,
            "mine": mine,
            "changes_requested": changes_requested(pr),
            "created_at": pr.created_at.isoformat(),
            "merged_at": pr.merged_at.isoformat() if pr.merged_at else None,
            "requested_users": list(pr.requested_users),
            "requested_teams": [team_names.get(slug, slug) for slug in pr.requested_teams],
        }
        if mine and state == "open":
            ignored = ctx.config.ignored_reviewers
            last = last_review_activity(pr, ignored)
            stale_after = ctx.config.stale_review_hours * 3600
            assignment = reviewer_assignment_reasons(
                pr,
                ctx.config.default_team_reviewer_assignment,
                ctx.config.team_reviewer_assignment,
                memberships,
            )
            fields["review_blockers"] = review_blockers(
                pr, me, ctx.config.policy_checks, ignored, assignment
            )
            fields["last_review_activity"] = last.isoformat() if last else None
            fields["stale_review_hours"] = ctx.config.stale_review_hours
            fields["failing_policy_checks"] = [
                c.name for c in policy_failures(pr, ctx.config.policy_checks)
            ]
            fields["progress"] = progress(pr, ignored)
            fields["review_decision"] = pr.review_decision
            fields["rollup_state"] = pr.rollup_state
            fields["mergeable"] = pr.mergeable
            fields["in_merge_queue"] = pr.in_merge_queue
            fields["failed_checks"] = [c.name for c in pr.checks if c.state in changes.FAILED]
            fields["review_stale"] = last is None or (ctx.now - last).total_seconds() >= stale_after
        pr_attrs = GitHubPullRequest.model_validate({**fields, "handle": f"#{pr.number}"})
        return Entity(
            ref=Ref(kind=PR, id=pr.url),
            title=pr.title,
            url=pr.url,
            state=state,
            lifecycle="open" if state == "open" else "done",
            mentions=[pr.head, pr.title, pr.body],
            # Another's PR, as one I'm asked to review, names what its author likes: an iced
            # ticket it names mustn't hide it from me.
            mentions_hide=mine,
            # Its labels are its tags, but the template's placeholders and those that say it's
            # worth splitting: they're about the PR's shape, not its topic.
            tags=[
                label
                for label in pr.labels
                if label not in ctx.config.placeholder_labels + ctx.config.split_pr_labels
            ],
            attrs=attrs(pr_attrs),
        )

    def entities(self, req: EntitiesRequest[GitHubConfig, GitHubSnapshot]) -> list[Entity]:
        """A `pull_request` for every PR in the snapshot, and a `person` (`<host>/<login>`)
        for me and for everyone whose email github knows."""
        ctx, snapshot = req.ctx, req.snapshot
        if snapshot is None:
            return []
        team_names = {ctx.config.slug(team): team for team in ctx.teams}
        groups = [
            (snapshot.mine, "open", True),
            (snapshot.merged_recent, "merged", True),
            (snapshot.closed_recent, "closed", True),
            (snapshot.direct_requests, "open", False),
            (snapshot.reviewed_by_me, "open", False),
            (snapshot.team_requests, "open", False),
            (snapshot.mentioned, "open", False),
        ]
        entities: dict[str, Entity] = {}
        for prs, state, mine in groups:
            for pr in prs:
                if pr.url not in entities:
                    entities[pr.url] = self._pr_entity(
                        ctx, pr, snapshot.login, state, mine, team_names, snapshot.team_memberships
                    )
        emails = PeopleRepository(ctx.db).emails()
        people = [
            Entity(
                ref=Ref(kind=PERSON, id=f"{ctx.config.host}/{login}"),
                title=login,
                attrs=attrs(
                    GitHubPerson(handle=login, me=login == snapshot.login, email=emails.get(login))
                ),
            )
            for login in dict.fromkeys([snapshot.login, *emails])
        ]
        return [*entities.values(), *people]

    def refresh_subjects(
        self, req: SubjectsRequest[GitHubConfig, GitHubSnapshot]
    ) -> Subjects[GitHubSnapshot] | None:
        """These PRs fetched now, in one request, placed in the stored lists as the searches
        would find them; None before the first full collect, or for a ref that isn't one of
        the host's PRs."""
        ctx, snapshot = req.ctx, req.snapshot
        ids = [pr_id_of(ctx.config.host, ref.id) for ref in req.refs]
        if snapshot is None or not ids or any(i is None for i in ids):
            return None
        fetched = client.fetch_prs(ctx.config.host, [i for i in ids if i is not None])
        urls = {pr.url for pr in fetched}
        return Subjects(
            placing.place(snapshot, fetched, _team_slugs(ctx)),
            [ref for ref in req.refs if ref.id in urls],
        )

    def relate(self, req: RelateRequest[GitHubConfig]) -> list[tuple[Ref, Ref]]:
        """Each of its PRs to the local branch it comes from (see `relations`)."""
        return relations.relations(req.graph, f"{self.name}.{PR}")

    def fetch(self, req: FetchRequest[GitHubConfig]) -> list[Entity]:
        """PRs by URL, with their state and `closed_at`: when they were merged or closed."""
        ctx, kind, ids = req.ctx, req.kind, req.ids
        if kind != PR:
            return []
        found: list[Entity] = []
        for url in ids:
            try:
                state, closed_at = client.pr_state(url)
            except CommandError as e:
                ctx.log.warning("couldn't fetch %s: %s", url, e)
                continue
            found.append(
                Entity(
                    ref=Ref(kind=PR, id=url),
                    title=url,
                    url=url,
                    state=state,
                    lifecycle="open" if state == "open" else "done",
                    attrs={"closed_at": closed_at.isoformat() if closed_at else None},
                )
            )
        return found

    def url_for(self, req: UrlRequest[GitHubConfig]) -> str | None:
        kind, id = req.kind, req.id
        return id if kind == PR else None

    def import_dir(self, req: ImportRequest[GitHubConfig]) -> list[str]:
        """Reviewers' emails from `reviewer-map.md`, replacing what an earlier import read."""
        path = req.dir / REVIEWER_MAP_FILE
        if not path.exists():
            return []
        emails = read_reviewer_map(path.read_text(encoding="utf-8-sig", errors="replace"))
        PeopleRepository(req.ctx.db).replace_imported(emails)
        return [f"imported {len(emails)} reviewers' emails"]

    def prune(self, req: PruneRequest[GitHubConfig]) -> list[str]:
        """Forget email lookups that found none `prune_days` ago, to try them again."""
        ctx = req.ctx
        cutoff = ctx.now - timedelta(days=ctx.prune_days)
        retried = PeopleRepository(ctx.db).forget_misses_before(cutoff)
        return [f"will look up {retried} reviewer email(s) again"] if retried else []

    def act_now(self, req: ActNowRequest[GitHubConfig, GitHubSnapshot]) -> list[Item]:
        """The panels' items with signals, their own or their panel's (one with neither is
        listed only), the feedback on my drafts too, though a draft's item is
        deferred; but not a deferred review: a draft isn't ready for one. Asking for a reviewer
        loses its effort while there's something else to do on the PR, which is worth doing
        before anyone looks at it, so it ranks below that and shows as "also" until then."""
        nominated = [
            item if item.signals else item.model_copy(update={"signals": panel.signals})
            for panel in req.panels
            if panel.act_now
            for item in panel.items
            if (item.signals or panel.signals)
            and not (
                item.deferred and (item.signals or panel.signals or Signals()).task == REVIEW_TASK
            )
        ]
        busy = {
            i.url for i in nominated if not (i.signals and i.signals.task == REQUEST_REVIEWER_TASK)
        }
        return [
            i.model_copy(update={"signals": i.signals.model_copy(update={"effort": "unknown"})})
            if i.signals and i.signals.task == REQUEST_REVIEWER_TASK and i.url in busy
            else i
            for i in nominated
        ]

    def actions(self) -> dict[str, ActionHandler]:
        return actions.ACTIONS

    def panels(self, req: PanelsRequest[GitHubConfig, GitHubSnapshot]) -> list[Panel]:
        ctx, snapshot, graph = req.ctx, req.snapshot, req.graph
        derived = DerivedRepository(ctx.db).load()
        return Builder(ctx.config, ctx.teams, ctx.now, snapshot, derived, graph).panels()
