"""The GitHub plugin."""

import argparse
from datetime import datetime, timedelta
from typing import ClassVar, cast

from ...api import (
    ActNowRequest,
    AfterCollectRequest,
    Asker,
    Changed,
    ChangedRequest,
    CliHandler,
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
    InitSetup,
    Item,
    KindSpec,
    LoadRequest,
    Panel,
    PanelsRequest,
    Plugin,
    PruneRequest,
    Ref,
    StoreRequest,
    UrlRequest,
    attrs,
    collect_into,
)
from ...api.kinds import PERSON
from ...api.shell import CommandError
from . import changes, cli, client, derive, notifications, placing, pushes
from .config import GitHubConfig
from .kinds import GitHubPerson, GitHubPullRequest
from .models import GitHubSnapshot, PullRequest
from .panels import PR, REVIEW_TASK, Builder
from .people import PeopleRepository
from .prs import (
    changes_requested,
    failing_checks,
    last_review_activity,
    progress,
    review_blockers,
)
from .setup import TEMPLATE, questions
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


def _team_slugs(ctx: Context[GitHubConfig]) -> list[str]:
    return [ctx.config.slug(team) for team in ctx.teams]


class GitHubPlugin(Plugin[GitHubConfig, GitHubSnapshot]):
    name = "github"
    title = "GitHub"
    init_template = TEMPLATE
    init_finds_teams = True
    config_model = GitHubConfig
    migrations: ClassVar[list[str]] = MIGRATIONS
    kinds: ClassVar[dict[str, KindSpec]] = {
        PR: KindSpec(GitHubPullRequest, fetchable=True, resolvable=True),
        PERSON: KindSpec(GitHubPerson),
    }
    cli_help = "import reviewers' emails from the skill's reviewer map"
    snapshot_model = GitHubSnapshot
    change_check_seconds: ClassVar[int | None] = notifications.DEFAULT_POLL_SECONDS
    watches: ClassVar[list[str]] = ["git"]
    notification_kinds: ClassVar[dict[str, str]] = changes.KINDS

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
                return placing.place(previous, fetched, _team_slugs(ctx))
        ctx.kv.set(LAST_FULL, ctx.now.isoformat())
        return client.collect(
            config.host,
            config.org,
            _team_slugs(ctx),
            config.history_days,
            ctx.now,
            config.search_scope,
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

    def changed(self, req: ChangedRequest[GitHubConfig]) -> bool | Changed:
        """Whether a branch of one of my open PRs moved (see `pushes`; asked right after each
        refresh of git, which it watches), or I have a notification about something in the
        org, polled as often as GitHub allows. Which PRs changed is kept for `collect`, to
        fetch just those, and asks for a refresh until it has."""
        ctx, graph = req.ctx, req.graph
        changed = self._branches_moved(ctx, graph) | self._notified(ctx)
        # Pending until `collect` fetches them: what's seen in the check core makes right
        # after a refresh (whose answer it ignores) still asks for a refresh at the next one.
        pending = self._note_changed(ctx, changed)
        if EVERYTHING in pending:
            return Changed(True, "every search")
        return Changed(bool(pending), f"{len(pending)} PR(s) to fetch")

    def _branches_moved(self, ctx: Context[GitHubConfig], graph: Graph) -> set[str]:
        now = pushes.tips(graph, PR, REPO)
        before = ctx.kv.get_json(BRANCHES_SEEN, object)
        if now != before:
            ctx.kv.set_json(BRANCHES_SEEN, now)
        if not isinstance(before, dict):  # the first look, or kept in an older form
            return set()
        return pushes.moved(cast(dict[str, str], before), now)

    def _notified(self, ctx: Context[GitHubConfig]) -> set[str]:
        """The PRs I have a notification about in the org (a review requested, a comment, a
        review), newer than the last look. A CI run names only its repo, so it stands for my
        open PRs there; what no search shows (an issue, a release, an alert) for nothing; and
        anything else for `EVERYTHING`. A "not modified" answer is cheap, and GitHub's poll
        interval is kept to."""
        wait = ctx.kv.get(NOTIFICATIONS_WAIT)
        if wait and ctx.now < datetime.fromisoformat(wait):
            return set()
        seen = ctx.kv.get(NOTIFICATIONS_SEEN)
        found = notifications.fetch(ctx.config.host, seen)
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
        for n in found.new_in(ctx.config.org, seen):
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
                ctx.config.reviewer_history_days,
                ctx.config.required_owner_marker,
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
            fields["review_blockers"] = review_blockers(pr, me, ctx.config.policy_checks, ignored)
            fields["last_review_activity"] = last.isoformat() if last else None
            fields["stale_review_hours"] = ctx.config.stale_review_hours
            fields["failing_policy_checks"] = [
                c.name for c in failing_checks(pr) if c.name in ctx.config.policy_checks
            ]
            fields["progress"] = progress(pr, ignored)
            fields["review_stale"] = last is None or (ctx.now - last).total_seconds() >= stale_after
        pr_attrs = GitHubPullRequest.model_validate({**fields, "handle": f"#{pr.number}"})
        return Entity(
            ref=Ref(kind=PR, id=pr.url),
            title=pr.title,
            url=pr.url,
            state=state,
            lifecycle="open" if state == "open" else "done",
            mentions=[pr.head, pr.title, pr.body],
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
                        ctx, pr, snapshot.login, state, mine, team_names
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

    def cli(self, parser: argparse.ArgumentParser) -> CliHandler:
        return cli.add_commands(parser)

    def prune(self, req: PruneRequest[GitHubConfig]) -> list[str]:
        """Forget email lookups that found none `prune_days` ago, to try them again."""
        ctx = req.ctx
        cutoff = ctx.now - timedelta(days=ctx.prune_days)
        retried = PeopleRepository(ctx.db).forget_misses_before(cutoff)
        return [f"will look up {retried} reviewer email(s) again"] if retried else []

    def act_now(self, req: ActNowRequest[GitHubConfig, GitHubSnapshot]) -> list[Item]:
        """The panels' items, but not reviews of drafts: they aren't ready for one, so the
        panel lists them last and Act now leaves them out."""
        return [
            item
            for item in super().act_now(req)
            if not (item.draft and item.signals and item.signals.task == REVIEW_TASK)
        ]

    def panels(self, req: PanelsRequest[GitHubConfig, GitHubSnapshot]) -> list[Panel]:
        ctx, snapshot, graph = req.ctx, req.snapshot, req.graph
        derived = DerivedRepository(ctx.db).load()
        return Builder(ctx.config, ctx.teams, ctx.now, snapshot, derived, graph).panels()
