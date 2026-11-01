"""`mfd init`'s questions for `[slack]`."""

from ...asking import Asker, InitSetup, channel, url

TEMPLATE = """\
# client_id = ""                        # the workspace's Slack app, to connect on slack's page
# ping_first = "Hi, would you mind reviewing {url} when you get a chance? Thanks in advance!"
# ping_follow_up = "Gentle bump on this review request"
# token_env = "SLACK_API_TOKEN"          # a token with users:read.email, to find Slack ids by email
# keychain_service = "move-forward-slack" # else the system's secret store, for your email
# team_channel = ""                      # a team's channel when none is set, e.g. "#team-{team}"
"""
EXAMPLE = '# workspace_url = "https://example.slack.com"\n'


def questions(ask: Asker, setup: InitSetup) -> dict[str, object]:
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
    return {
        **({"workspace_url": workspace} if workspace else {}),
        **({"teams": channels} if channels else {}),
    }
