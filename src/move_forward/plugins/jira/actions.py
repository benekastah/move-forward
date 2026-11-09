"""Jira's buttons: change a ticket's status."""

from ...api import ActionError, ActionHandler, ActionRequest, http
from . import client
from .config import JiraConfig


def transition(req: ActionRequest[JiraConfig]) -> str:
    """Move the ticket `key` to the status `to`, or else one of the `also` names (one per
    line): the first its workflow offers."""
    ctx, fields = req.ctx, req.fields
    key, to = req.field("key"), req.field("to")
    names = [to, *fields.get("also", "").splitlines()]
    if not client.is_key(key):
        raise ActionError(f"not a ticket key: {key}")
    try:
        moved = client.transition(ctx.config, ctx.email, key, names)
    except LookupError as e:
        raise ActionError(str(e)) from e
    except http.RequestError as e:
        if e.status is not None:
            raise ActionError(f"Jira refused to move {key} to {to} (HTTP {e.status})") from e
        raise ActionError(f"couldn't reach Jira: {e.reason}") from e
    return f"moved {key} to {moved}"


ACTIONS: dict[str, ActionHandler] = {"transition": transition}
