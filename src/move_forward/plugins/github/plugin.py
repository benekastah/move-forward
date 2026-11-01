"""The GitHub plugin."""

import argparse
import logging
from typing import ClassVar

from pydantic import BaseModel

from ...graph import Entity, Graph, Ref, attrs
from ...page import Panel
from ...plugin import CliHandler, Context, Plugin, collect_into
from ...shell import CommandError
from . import cli, client, derive
from .config import GitHubConfig
from .kinds import PersonAttrs, PrAttrs
from .models import GitHubSnapshot, PullRequest
from .panels import PR, Builder
from .people import PeopleRepository
from .prs import (
    changes_requested,
    last_review_activity,
    review_blockers,
    ticket_keys,
)
from .snapshots import DerivedRepository, PrRepository
from .tables import MIGRATIONS

log = logging.getLogger(__name__)

DERIVED = "derived"
"""`after_collect`'s part of the refresh, `github.derived`: team scores and reviewer
suggestions. Its status is recorded under that name; what it found goes to its tables."""
PERSON = "person"
REPO = "repo"
TICKET = "ticket"


def _team_slugs(ctx: Context[GitHubConfig]) -> list[str]:
    return [ctx.config.slug(team) for team in ctx.teams]


class GitHubPlugin(Plugin[GitHubConfig, GitHubSnapshot]):
    name = "github"
    title = "GitHub"
    config_model = GitHubConfig
    migrations: ClassVar[list[str]] = MIGRATIONS
    kinds: ClassVar[dict[str, type[BaseModel]]] = {PR: PrAttrs, PERSON: PersonAttrs}
    cli_help = "import reviewers' emails from the skill's reviewer map"
    snapshot_model = GitHubSnapshot

    def collect(self, ctx: Context[GitHubConfig]) -> GitHubSnapshot:
        return client.collect(ctx.config.org, _team_slugs(ctx), ctx.config.history_days, ctx.now)

    def store(self, ctx: Context[GitHubConfig], snapshot: GitHubSnapshot) -> None:
        PrRepository(ctx.db).replace(snapshot)

    def load(self, ctx: Context[GitHubConfig]) -> GitHubSnapshot | None:
        return PrRepository(ctx.db).load()

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
                    email = client.commit_email(pr.repo, login, domains)
                except Exception as e:  # noqa: BLE001
                    log.warning("email lookup for %s failed: %s", login, e)
                    continue
                people.save_looked_up(login, email)
                known[login] = email

    def after_collect(self, ctx: Context[GitHubConfig], graph: Graph) -> None:
        """Look up reviewers' emails, then score team PRs and suggest reviewers from the
        local checkouts the graph's `repo` entities describe."""
        gh = self.load(ctx)
        if gh is None:
            return
        self._lookup_emails(ctx, gh)
        checkouts = [
            derive.Checkout.model_validate({"github_repo": repo.ref.id, **repo.attrs})
            for repo in graph.of_kind(REPO)
        ]
        collect_into(
            ctx.db,
            f"{self.name}.{DERIVED}",
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
            last = last_review_activity(pr)
            stale_after = ctx.config.stale_review_hours * 3600
            fields["review_blockers"] = review_blockers(pr, me, ctx.config.review_checks)
            fields["last_review_activity"] = last.isoformat() if last else None
            fields["review_stale"] = last is None or (ctx.now - last).total_seconds() >= stale_after
        pr_attrs = PrAttrs.model_validate(fields)
        return Entity(
            ref=Ref(kind=PR, id=pr.url),
            title=pr.title,
            url=pr.url,
            state=state,
            refs=[Ref(kind=TICKET, id=key) for key in ticket_keys(pr)],
            attrs=attrs(pr_attrs),
        )

    def entities(self, ctx: Context[GitHubConfig], snapshot: GitHubSnapshot | None) -> list[Entity]:
        """`pr` for every PR in the snapshot, and a `person` for me and for everyone whose
        email github knows."""
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
                ref=Ref(kind=PERSON, id=login),
                title=login,
                attrs=attrs(PersonAttrs(me=login == snapshot.login, email=emails.get(login))),
            )
            for login in dict.fromkeys([snapshot.login, *emails])
        ]
        return [*entities.values(), *people]

    def fetch(self, ctx: Context[GitHubConfig], kind: str, ids: list[str]) -> list[Entity]:
        """PRs by URL, with their state and `closed_at`: when they were merged or closed."""
        if kind != PR:
            return []
        found: list[Entity] = []
        for url in ids:
            try:
                state, closed_at = client.pr_state(url)
            except CommandError as e:
                log.warning("couldn't fetch %s: %s", url, e)
                continue
            found.append(
                Entity(
                    ref=Ref(kind=PR, id=url),
                    title=url,
                    url=url,
                    state=state,
                    attrs={"closed_at": closed_at.isoformat() if closed_at else None},
                )
            )
        return found

    def url_for(self, ctx: Context[GitHubConfig], kind: str, id: str) -> str | None:
        return id if kind == PR else None

    def cli(self, parser: argparse.ArgumentParser) -> CliHandler:
        return cli.add_commands(parser)

    def panels(
        self, ctx: Context[GitHubConfig], snapshot: GitHubSnapshot | None, graph: Graph
    ) -> list[Panel]:
        derived = DerivedRepository(ctx.db).load()
        return Builder(ctx.config, ctx.teams, ctx.now, snapshot, derived, graph).panels()
