"""`mfd init`'s questions for `[slack]`."""

from ...api import Asker, InitSection, InitSetup, channel, url

TABLES = """\
# Where review pings to a team go, by its name in `teams`.
# [slack.teams]
# my-team = "#team-my-team"
"""
EXAMPLE = InitSection({"workspace_url": "https://example.slack.com"})


def questions(ask: Asker, setup: InitSetup) -> InitSection:
    """The workspace, for ping links, and a channel for each of my teams' review pings."""
    workspace = ask(
        "Slack workspace URL, for ping links; it's under the workspace name in Slack's menu"
        " (e.g. https://acme.slack.com; blank for none)",
        "",
        url,
    )
    channels: dict[str, object] = {}
    for team in setup.teams:
        answer = ask(
            f"Slack channel for review pings to {team} (e.g. #team-{team}; blank for none)",
            "",
            channel,
        )
        if answer:
            channels[team] = answer
    return InitSection(
        {
            **({"workspace_url": workspace} if workspace else {}),
            **({"teams": channels} if channels else {}),
        }
    )
