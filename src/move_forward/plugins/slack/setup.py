"""`mfd init`'s questions for `[slack]`."""

from move_forward.api import Asker, Init, InitSection, InitSetup, url

TABLES = """\
# Where review pings to a team go, by its slug, as PRs name it.
# [slack.teams]
# my-team = "#team-my-team"
"""
EXAMPLE = InitSection({"workspace_url": "https://example.slack.com"})


def questions(ask: Asker, setup: InitSetup) -> InitSection:
    """The workspace, for ping links. My teams' channels are set once github has found the
    teams, on `/slack/contacts` or with `mfd slack destinations --discover`."""
    workspace = ask(
        "Slack workspace URL, for ping links; it's under the workspace name in Slack's menu"
        " (e.g. https://acme.slack.com; blank for none)",
        "",
        url,
    )
    return InitSection({"workspace_url": workspace} if workspace else {})


INIT = Init(questions=questions, example=EXAMPLE, tables=TABLES, example_on=True)
