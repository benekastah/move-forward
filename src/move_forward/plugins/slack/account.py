"""Slack's page, `/slack`: setting up the app, connecting to it, and what it's connected as;
and the pages the sign-in goes through."""

from importlib import resources

from ...api import Action, ActionError, ActionRequest, Context, PageRequest, Response, html
from . import oauth, scopes
from .config import SlackConfig

SLACK_PATH = "/slack"
MANIFEST = "manifest.json"
CONNECT = Action(name="slack/connect", label="Connect Slack", fields={})
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
    e = html.e
    redirect = ctx.page_url(oauth.CALLBACK)
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


def page(req: PageRequest[SlackConfig]) -> str:
    """Slack's page: what it's connected as, or how to connect, or how to set the app up."""
    ctx, query = req.ctx, req.query
    e = html.e
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
            + html.buttons([DISCONNECT], SLACK_PATH)
        )
    else:
        if lost:
            parts.append(f"<p class='notice warn'>The connection stopped working: {e(lost)}.</p>")
        if ctx.config.client_id:
            parts.append(
                "<h2>Connect</h2><p>Sign in to the workspace's Slack app, for your own"
                " token.</p>" + html.buttons([CONNECT], oauth.CONNECT_PATH)
            )
        parts.append(_setup(ctx))
    body = "<article class='panel padded'>" + "".join(parts) + "</article>"
    return html.page("Slack", "slack", body)


def connect(req: PageRequest[SlackConfig]) -> str | Response | None:
    """Send the browser to Slack for the sign-in the Connect button started (see
    `connect_action`); a read that starts nothing, so a link or an image can't overwrite a
    sign-in under way. None (not found) without a `client_id`."""
    ctx = req.ctx
    if not ctx.config.client_id:
        return None
    url = oauth.pending_url(ctx)
    if url is None:
        body = (
            "<article class='panel padded'><p>No sign-in under way: use the Connect button on"
            f" <a href='{SLACK_PATH}'>Slack's page</a>.</p></article>"
        )
        return html.page("Connect Slack", "slack", body)
    return Response.redirect(url)


def connect_action(req: ActionRequest[SlackConfig]) -> str:
    """Start signing in: a fresh `state` and verifier replace any sign-in started before.
    A button, so it is a POST, which the server only takes from the dashboard's own pages."""
    ctx = req.ctx
    if not ctx.config.client_id:
        raise ActionError("set client_id in [slack] first")
    oauth.start(ctx)
    return "taking you to Slack"


def callback(req: PageRequest[SlackConfig]) -> str:
    """Where Slack sends the browser back: finish signing in, or say why not. Served
    without the dashboard's cookie (see `Plugin.sign_in_pages`): `oauth.finish` checks the
    sign-in's own `state` before anything else."""
    ctx, query = req.ctx, req.query
    e = html.e
    try:
        found = oauth.finish(ctx, query)
    except oauth.SignInError as err:
        body = f"<p>Not connected: {e(err)}.</p>"
    else:
        body = f"<p>Connected to {e(found.team_name or found.team_id)}.</p>"
    body += f"<p><a class='button' href='{SLACK_PATH}'>Back to Slack's page</a></p>"
    return html.page("Slack", "slack", f"<article class='panel padded'>{body}</article>")


def disconnect_action(req: ActionRequest[SlackConfig]) -> str:
    """Revoke and forget the app's token."""
    ctx = req.ctx
    if oauth.connection(ctx) is None:
        raise ActionError("Slack isn't connected")
    oauth.disconnect(ctx)
    return "disconnected Slack"
