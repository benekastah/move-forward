"""`mfd init`'s questions for `[jira]`."""

from ...api import Asker, InitSetup, https_url

EXAMPLE = '# site = "https://example.atlassian.net"\n'


def _required(answer: str) -> str:
    answer = https_url(answer)
    if not answer:
        raise ValueError("the site is required")
    return answer


def questions(ask: Asker, setup: InitSetup) -> dict[str, object]:
    return {"site": ask("Jira site URL (e.g. https://acme.atlassian.net)", "", _required)}
