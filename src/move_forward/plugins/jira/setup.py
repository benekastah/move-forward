"""`mfd init`'s questions for `[jira]`."""

from ...api import Asker, Init, InitSection, InitSetup, https_url

EXAMPLE = InitSection({"site": "https://example.atlassian.net"})


def _required(answer: str) -> str:
    answer = https_url(answer)
    if not answer:
        raise ValueError("the site is required")
    return answer


def questions(ask: Asker, setup: InitSetup) -> InitSection:
    return InitSection(
        {"site": ask("Jira site URL (e.g. https://acme.atlassian.net)", "", _required)}
    )


INIT = Init(
    questions=questions,
    example=EXAMPLE,
    token_help=(
        "create an API token at https://id.atlassian.com/manage-profile/security/api-tokens,"
        " then store it with `mfd token set jira` (or export it as JIRA_API_TOKEN)"
    ),
)
