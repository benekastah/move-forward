"""Slack's page, `/slack`: setting up the app, connecting to it, and what it's connected as;
and the pages the sign-in goes through."""

import json
from importlib import resources

from ... import render
from ...graph import Graph
from ...page import Action
from ...plugin import ActionError, Context
from . import oauth, scopes
from .config import SlackConfig

SLACK_PATH = "/slack"
MANIFEST = "manifest.json"
DISCONNECT = Action(
    name="slack/disconnect",
    label="Disconnect",
    fields={},
    confirm="Disconnect Slack? Its token is revoked and forgotten.",
)


def manifest() -> str:
    """The app's manifest, as checked in next to this module."""
    return resources.files(__package__ or "").joinpath(MANIFEST).read_text()


def _setup(ctx: Context[SlackConfig]) -> str:
    """How to make the app, once per workspace, and how to point the dashboard at it."""
    e = render.e
    redirect = ctx.dashboard_url + oauth.CALLBACK_PATH
    return (
        "<h2>Set up the Slack app</h2>"
        "<p>One app serves the whole workspace; someone makes it once:</p><ol>"
        "<li>At <a href='https://api.slack.com/apps?new_app=1' target='_blank'"
        " rel='noopener'>api.slack.com/apps</a>, choose <em>Create New App</em>, then"
        " <em>From a manifest</em>, pick the workspace and paste the manifest below.</li>"
        "<li>Install it to the workspace (an admin may need to approve it); on Enterprise"
        " Grid, install it to the organization.</li>"
        "<li>Copy its <em>Client ID</em> from <em>Basic Information</em> and share it.</li>"
        "</ol><p>Then everyone sets <code>client_id</code> in <code>[slack]</code> and"
        " connects here. The manifest's redirect URLs are for the default port; on another"
        f" port, add <code>{e(redirect)}</code> to the app's redirect URLs.</p>"
        f"<pre class='manifest'>{e(manifest())}</pre>"
    )


def page(ctx: Context[SlackConfig], graph: Graph, query: dict[str, list[str]]) -> str:
    """Slack's page: what it's connected as, or how to connect, or how to set the app up."""
    e = render.e
    found = oauth.connection(ctx)
    lost = ctx.kv.get(oauth.LOST)
    notice = query.get("notice", [""])[0]
    parts = [f"<p class='notice'>{e(notice)}</p>"] if notice else []
    if found is not None:
        missing = {f: m for f in scopes.FEATURE_SCOPES if (m := scopes.missing(f, found.scopes))}
        lacking = "".join(
            f"<li>{e(feature)} needs {e(', '.join(needed))}: reconnect once the app has it</li>"
            for feature, needed in missing.items()
        )
        parts.append(
            "<h2>Connected</h2>"
            f"<p>As <code>{e(found.user_id)}</code> in {e(found.team_name or found.team_id)},"
            f" with {e(', '.join(found.scopes) or 'no scopes')}.</p>"
            + (f"<ul>{lacking}</ul>" if lacking else "")
            + render.buttons([DISCONNECT], SLACK_PATH)
        )
    else:
        if lost:
            parts.append(f"<p class='notice warn'>The connection stopped working: {e(lost)}.</p>")
        if ctx.config.client_id:
            parts.append(
                "<h2>Connect</h2><p>Sign in to the workspace's Slack app, for your own"
                " token.</p>"
                f"<p><a class='button' href='{oauth.CONNECT_PATH}'>Connect Slack</a></p>"
            )
        parts.append(_setup(ctx))
    body = "<article class='panel padded'>" + "".join(parts) + "</article>"
    return render.page("Slack", "slack", body)


def connect(ctx: Context[SlackConfig], graph: Graph, query: dict[str, list[str]]) -> str | None:
    """Start signing in and send the browser to Slack; None (not found) without a
    `client_id`."""
    if not ctx.config.client_id:
        return None
    url = oauth.start(ctx)
    # The URL is ours, built with urlencode; JSON-quoted, with "</" broken so it can't end
    # the script.
    quoted = json.dumps(url).replace("</", "<\\/")
    script = f"location.replace({quoted});"
    body = (
        "<article class='panel padded'><p>Taking you to Slack to sign in…</p>"
        f"<p><a class='button' href='{render.e(url)}'>Continue to Slack</a></p></article>"
    )
    return render.page("Connect Slack", "slack", body, script)


def callback(ctx: Context[SlackConfig], graph: Graph, query: dict[str, list[str]]) -> str:
    """Where Slack sends the browser back: finish signing in, or say why not. Served
    without the dashboard's cookie (see `Plugin.sign_in_pages`): `oauth.finish` checks the
    sign-in's own `state` before anything else."""
    e = render.e
    try:
        found = oauth.finish(ctx, query)
    except oauth.SignInError as err:
        body = f"<p>Not connected: {e(err)}.</p>"
    else:
        body = f"<p>Connected to {e(found.team_name or found.team_id)}.</p>"
    body += f"<p><a class='button' href='{SLACK_PATH}'>Back to Slack's page</a></p>"
    return render.page("Slack", "slack", f"<article class='panel padded'>{body}</article>")


def disconnect_action(ctx: Context[SlackConfig], graph: Graph, fields: dict[str, str]) -> str:
    """Revoke and forget the app's token."""
    if oauth.connection(ctx) is None:
        raise ActionError("Slack isn't connected")
    oauth.disconnect(ctx)
    return "disconnected Slack"
