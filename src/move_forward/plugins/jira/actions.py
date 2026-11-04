"""Jira's buttons: change a ticket's status."""

import urllib.error

from ...graph import Graph
from ...plugin import ActionError, ActionHandler, Context
from . import client
from .config import JiraConfig


def transition(ctx: Context[JiraConfig], graph: Graph, fields: dict[str, str]) -> str:
    """Move the ticket `key` to the status `to`, or else one of the `also` names (one per
    line): the first its workflow offers."""
    key, to = fields["key"], fields["to"]
    names = [to, *fields.get("also", "").splitlines()]
    if not client.is_key(key):
        raise ActionError(f"not a ticket key: {key}")
    try:
        client.transition(ctx.config, ctx.email, key, names)
    except LookupError as e:
        raise ActionError(str(e)) from e
    except urllib.error.HTTPError as e:
        raise ActionError(f"Jira refused to move {key} to {to} (HTTP {e.code})") from e
    except urllib.error.URLError as e:
        raise ActionError(f"couldn't reach Jira: {e.reason}") from e
    return f"moved {key} to {to}"


ACTIONS: dict[str, ActionHandler] = {"transition": transition}
