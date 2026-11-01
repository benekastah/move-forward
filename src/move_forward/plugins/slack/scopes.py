"""The Slack app's user scopes, by what slack does with them. `manifest.json`, which makes
the app, asks for exactly these (a test checks), and a feature runs only when the
connection was granted its scopes."""

FEATURE_SCOPES: dict[str, list[str]] = {
    "lookups": ["users:read", "users:read.email"],
}
"""Each feature's user scopes. `lookups` finds requested reviewers' Slack ids by email."""


def all_scopes() -> list[str]:
    """Every feature's scopes, once each, sorted: what the app asks for."""
    return sorted({scope for scopes in FEATURE_SCOPES.values() for scope in scopes})


def missing(feature: str, granted: list[str]) -> list[str]:
    """The scopes `feature` needs that `granted` lacks."""
    return [scope for scope in FEATURE_SCOPES[feature] if scope not in granted]
