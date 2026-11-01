"""Server-side HTML for the dashboard and focus mode, and the frame of plugins' pages."""

from html import escape
from urllib.parse import urlencode

from .page import Action, Group, Item, Link, Panel, age
from .panels import ACT_NOW, FOCUS_ALL, Dashboard

PAGE_RELOAD_SECONDS = 60
REFRESHING_RELOAD_SECONDS = 10
"""Reload faster while a refresh runs, so the new data shows up soon after it lands."""
OPEN_SECTIONS_SCRIPT = (
    # The server renders each <details> open or closed from the `open` query parameter
    # (`?open=act,github.feedback`). This keeps that parameter current: one debounced
    # replaceState per burst of toggles (browsers throttle rapid history updates), and
    # only when the state changed (browsers fire toggle at load for sections that start
    # open); the query is carried along when a button's form posts (so the redirect back
    # keeps it), and by the periodic reload.
    "document.addEventListener('DOMContentLoaded', () => {"
    " const sections = [...document.querySelectorAll('details[data-key]')];"
    " const state = () => sections.filter((s) => s.open).map((s) => s.dataset.key).join(',');"
    " let saved = state();"  # browsers fire toggle for <details open> at load; ignore those
    " let pending = 0;"
    " const save = () => { clearTimeout(pending); pending = setTimeout(() => {"
    "  const open = state(); if (open === saved) return; saved = open;"
    "  history.replaceState(null, '', '?open=' + open); }, 100); };"
    " for (const s of sections) s.addEventListener('toggle', save);"
    " for (const f of document.forms) f.addEventListener('submit', () => {"
    "  f.action = f.action.split('?')[0] + location.search; });"
    " const seconds = Number(document.body.dataset.reload);"
    " if (seconds) setTimeout(() => location.reload(), seconds * 1000);"
    " });"
)
"""Keeps the open sections in the URL across reloads and button presses; see inside."""
LAYOUT_SCRIPT = (
    # Dragging a group's or a panel's summary moves it among its siblings (a panel stays in
    # its group, and Act now, which has no data-drag, stays first), then posts the page's
    # whole order to /layout. Without the script the page keeps its order.
    "document.addEventListener('DOMContentLoaded', () => {"
    " let dragged = null;"
    " const save = () => {"
    "  const groups = [...document.querySelectorAll('main > details.group')];"
    "  const panels = {};"
    "  for (const g of groups) panels[g.dataset.key] ="
    "   [...g.querySelectorAll(':scope > .panels > details.panel')].map((p) => p.dataset.key);"
    "  fetch('/layout', { method: 'POST', headers: { 'Content-Type': 'application/json' },"
    "   body: JSON.stringify({ groups: groups.map((g) => g.dataset.key), panels }) }); };"
    " for (const el of document.querySelectorAll('details[data-drag]')) {"
    "  const summary = el.querySelector(':scope > summary');"
    "  summary.draggable = true;"
    "  summary.addEventListener('dragstart', (e) => { dragged = el;"
    "   e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', el.dataset.key); });"
    "  summary.addEventListener('dragend', () => { dragged = null; });"
    "  const sibling = () => dragged && dragged !== el && dragged.parentElement === el.parentElement;"
    "  el.addEventListener('dragover', (e) => { if (sibling()) e.preventDefault(); });"
    "  el.addEventListener('drop', (e) => {"
    "   if (!sibling()) return;"
    "   e.preventDefault(); e.stopPropagation();"
    "   const box = el.getBoundingClientRect();"
    "   el.parentElement.insertBefore(dragged, e.clientY > box.top + box.height / 2 ? el.nextSibling : el);"
    "   save(); });"
    " }"
    " });"
)
"""Reorders groups and panels by dragging their summaries, and stores the order; see inside."""
FOCUS_KEYS_SCRIPT = (
    "document.addEventListener('keydown', (e) => {"
    " if (e.target.closest('input, textarea')) return;"
    " const link = document.querySelector(e.key === 'ArrowLeft' ? 'a.prev' : e.key === 'ArrowRight'"
    " ? 'a.next' : 'nothing'); if (link) link.click(); });"
)
"""Focus mode: the left and right arrow keys go to the previous and next item."""
LOGO = "<a href='/' class='logo'><img src='/logo.svg' alt=''> Move forward</a>"
"""The header's home link: the arrow (the favicon without its circle) and the name."""
WARNING_BADGE_WORDS = ("conflict", "blocked", "uncommitted", "draft", "failing", "iced", "unpushed")


def _e(text: object) -> str:
    return escape(str(text), quote=True)


def e(text: object) -> str:
    """`text` escaped for HTML text and attribute values, for plugins' pages."""
    return _e(text)


def page(title: str, crumb: str, body: str, script: str = "") -> str:
    """A whole single-column page in the dashboard's look, for plugins' pages: the header
    reads "Move forward / `crumb`", and `body` (HTML) goes in its main column."""
    return (
        _head(title, None)
        + f"<header><h1>{LOGO} / {_e(crumb)}</h1></header><main class='single'>{body}</main>"
        + (f"<script>{script}</script>" if script else "")
        + "</body></html>"
    )


def _head(title: str, reload_seconds: int | None) -> str:
    """Up to and including the `<body>` tag.

    With `reload_seconds`, the page reloads itself from JavaScript, which keeps the URL
    fragment (and so the open sections); `<noscript>` falls back to a meta refresh.
    """
    if reload_seconds:
        reload = (
            f'<noscript><meta http-equiv="refresh" content="{reload_seconds}"></noscript>'
            f"<script>{OPEN_SECTIONS_SCRIPT}</script><script>{LAYOUT_SCRIPT}</script>"
        )
        body = f"<body data-reload='{reload_seconds}'>"
    else:
        reload = ""
        body = "<body>"
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{_e(title)}</title>{reload}"
        "<link rel='icon' href='/favicon.svg' type='image/svg+xml'>"
        f"<link rel='stylesheet' href='/style.css'></head>{body}"
    )


def _linkable(url: str | None) -> bool:
    """Whether `url` may be a link: http(s), or a path on this dashboard. Anything else (a
    `javascript:` URL, say, should one ever reach a plugin's data) is shown as text."""
    return url is not None and (
        url.startswith(("https://", "http://"))
        or (url.startswith("/") and not url.startswith("//"))
    )


def banner(text: str, urgency: str | None) -> str:
    """A bar across the page for something that must be done: "notice" (blue), "soon"
    (yellow) or "now" (red); nothing without an urgency."""
    if not text or urgency is None:
        return ""
    return f"<div class='banner {_e(urgency)}' role='alert'>{_e(text)}</div>"


def _badge(text: str) -> str:
    warn = any(word in text.lower() for word in WARNING_BADGE_WORDS)
    return f"<span class='badge{' warn' if warn else ''}'>{_e(text)}</span>"


def _link_action(action: Action) -> str:
    if not _linkable(action.href):
        return ""
    title = f" title='{_e(action.title)}'" if action.title else ""
    return (
        f"<a class='button link-action{' muted' if action.muted else ''}'"
        f" href='{_e(action.href)}'{title}>{_e(action.label)}</a>"
    )


def _action(action: Action, back: str = "") -> str:
    """A button's form. `back` is where the action redirects afterwards; default the page."""
    fields = "".join(
        f"<input type='hidden' name='{_e(name)}' value='{_e(value)}'>"
        for name, value in action.fields.items()
    )
    confirm = f" onsubmit='return confirm({_e(repr(action.confirm))})'" if action.confirm else ""
    query = "?" + urlencode({"back": back}) if back else ""
    if action.checkbox:
        control = (
            f"<label class='check{' suggested' if action.suggested else ''}'>"
            f"<input type='checkbox'{' checked' if action.checked else ''}"
            f" onchange='this.form.requestSubmit()'> {_e(action.label)}</label>"
        )
    else:
        control = f"<button type='submit' class='secondary small'>{_e(action.label)}</button>"
        if action.input:
            control = (
                f"<input type='text' name='input' placeholder='{_e(action.input)}' required>"
                + control
            )
    return (
        f"<form method='post' action='/actions/{_e(action.name)}{query}' class='action'{confirm}>"
        f"{fields}{control}</form>"
    )


def _actions(actions: list[Action], back: str = "") -> str:
    buttons = "".join(_action(a, back) for a in actions if a.href is None)
    return f"<div class='actions'>{buttons}</div>" if actions else ""


def _link(link: Link) -> str:
    """A link; a PR's gets its state as a class (merged and closed are struck through)."""
    classes = [c for c, on in (("strong", link.strong), (link.pr_state, True)) if on and c]
    cls = f" class='{' '.join(classes)}'" if classes else ""
    if not _linkable(link.url):
        return f"<span{cls}>{_e(link.text)}</span>"
    return f"<a href='{_e(link.url)}'{cls} target='_blank' rel='noopener'>{_e(link.text)}</a>"


def _item(item: Item, back: str = "") -> str:
    title = (
        f"<a href='{_e(item.url)}' target='_blank' rel='noopener'>{_e(item.title)}</a>"
        if _linkable(item.url)
        else _e(item.title)
    )
    parts = [
        "<li class='item'><div class='item-head'>",
        f"<div class='item-title'>{title}</div>"
        + "".join(_link_action(a) for a in item.actions if a.href is not None)
        + "</div>",
    ]
    if item.subtitle:
        parts.append(f"<div class='item-subtitle'>{_e(item.subtitle)}</div>")
    if item.badges:
        parts.append("<div class='badges'>" + "".join(_badge(b) for b in item.badges) + "</div>")
    if item.details:
        parts.append(
            "<ul class='details'>" + "".join(f"<li>{_e(d)}</li>" for d in item.details) + "</ul>"
        )
    if item.links:
        links = "".join(_link(link) for link in item.links)
        parts.append(f"<div class='links'>{links}</div>")
    parts.append(_actions(item.actions, back))
    parts.append("</li>")
    return "".join(parts)


def _open(key: str, default: bool, open_keys: set[str] | None) -> str:
    """The `open` attribute: from the `open` query parameter when given, else the default."""
    is_open = default if open_keys is None else key in open_keys
    return " open" if is_open else ""


def _panel(panel: Panel, open_keys: set[str] | None) -> str:
    open_attr = _open(panel.key, bool(panel.items), open_keys)
    items = "".join(_item(item) for item in panel.items)
    return (
        f"<details class='panel{'' if panel.items else ' empty'}' data-key='{_e(panel.key)}'"
        f" data-drag{open_attr}>"
        f"<summary><h2>{_e(panel.title)}</h2><span class='count'>{len(panel.items)}</span>"
        f"<span class='description'>{_e(panel.description)}</span></summary>"
        f"{_actions(panel.actions)}<ul class='items'>{items}</ul></details>"
    )


def _group(group: Group, dashboard: Dashboard, open_keys: set[str] | None) -> str:
    """A group section; panel keys the dashboard left out (e.g. Stacks without git-stack) are skipped."""
    panels = [p for key in group.panel_keys if (p := dashboard.find_panel(key))]
    count = sum(len(panel.items) for panel in panels)
    return (
        f"<details class='group' data-key='{_e(group.key)}'"
        + (" data-drag" if group.key != ACT_NOW else "")
        + f"{_open(group.key, group.open, open_keys)}>"
        f"<summary><h2>{_e(group.title)}</h2><span class='count'>{count}</span></summary>"
        "<div class='panels'>"
        + "".join(_panel(panel, open_keys) for panel in panels)
        + "</div></details>"
    )


def _sources(dashboard: Dashboard) -> str:
    spans: list[str] = []
    for name, status in dashboard.sources.items():
        if status.error:
            spans.append(
                f"<span class='error'><strong>{_e(name)}:</strong> error ({_e(status.error)})</span>"
            )
        else:
            spans.append(
                f"<span><strong>{_e(name)}:</strong>"
                f" {age(dashboard.generated_at, status.fetched_at)} ago</span>"
            )
    if dashboard.login:
        spans.append(f"<span>as {_e(dashboard.login)}</span>")
    return "".join(spans)


def dashboard_page(
    dashboard: Dashboard,
    refreshing: bool,
    notice: str = "",
    open_keys: set[str] | None = None,
    warning: str = "",
    urgency: str | None = None,
) -> str:
    """The page.

    `notice` is the outcome of an action just run, shown in the header. `open_keys` are the
    sections the `open` query parameter says are open; None means the parameter was absent
    and the defaults apply. `warning` is something to fix, e.g. certificates ending soon,
    shown in the header, and across the page too with an `urgency` (see `banner`).
    """
    status = "refreshing…" if refreshing else f"built {dashboard.generated_at:%H:%M}"
    reload_seconds = REFRESHING_RELOAD_SECONDS if refreshing else PAGE_RELOAD_SECONDS
    notice_html = f"<span class='notice'>{_e(notice)}</span>" if notice else ""
    notice_html += f"<span class='notice warn'>{_e(warning)}</span>" if warning else ""
    return (
        _head("Move forward", reload_seconds) + f"<header><h1>{LOGO}</h1><div class='header-right'>"
        f"{notice_html}<span>{_e(status)}</span>"
        "<a class='button' href='/focus'>Focus</a>"
        "<form method='post' action='/refresh'><button type='submit'"
        f"{' disabled' if refreshing else ''}>Refresh</button></form></div></header>"
        + banner(warning, urgency)
        + "<main>"
        + "".join(_group(group, dashboard, open_keys) for group in dashboard.groups)
        + "</main>"
        f"<footer>{_sources(dashboard)}<form method='post' action='/layout/reset'>"
        "<button type='submit' class='secondary'>Reset layout</button></form></footer>"
        "</body></html>"
    )


def focus_url(category: str, index: int) -> str:
    return "/focus?" + urlencode({"cat": category, "i": index})


def _focus_category(dashboard: Dashboard, key: str, title: str, cls: str) -> str:
    count = len(dashboard.focus_items(key) or [])
    if not count:
        return f"<li class='{cls} empty'><span>{_e(title)}</span><span class='count'>0</span></li>"
    return (
        f"<li class='{cls}'><a href='{_e(focus_url(key, 0))}'>{_e(title)}</a>"
        f"<span class='count'>{count}</span></li>"
    )


def focus_categories_page(dashboard: Dashboard, alert: str = "") -> str:
    """Focus mode's first page: "All", then each group with its panels, each with a count.
    `alert` is a `banner` to show across it."""
    rows = [_focus_category(dashboard, FOCUS_ALL, "All", "category")]
    for group in dashboard.groups:
        rows.append(_focus_category(dashboard, group.key, group.title, "category"))
        rows.extend(
            _focus_category(dashboard, panel.key, panel.title, "category sub")
            for key in group.panel_keys
            if (panel := dashboard.find_panel(key))
        )
    return (
        _head("Focus", None) + f"<header><h1>{LOGO} / focus</h1></header>{alert}"
        "<main class='single'><article class='panel padded'><h2>What to focus on?</h2>"
        "<p>One item at a time, in the skill's order of priority.</p>"
        f"<ul class='categories'>{''.join(rows)}</ul></article></main></body></html>"
    )


def focus_page(
    dashboard: Dashboard,
    category: str,
    index: int,
    notice: str = "",
    done: tuple[str, Item] | None = None,
    alert: str = "",
) -> str | None:
    """One item of a focus category with previous and next links; None for an unknown category.

    An index past the end (the list shrank since the link was made) shows the last item.
    `notice` is the outcome of an action just run from this page. `done` is an item an action
    just changed, e.g. a todo ticked off, with its panel's key: it's shown in place of the
    item at `index` (which is now the one after it), so the change can be undone.
    """
    title = dashboard.focus_title(category)
    items = dashboard.focus_items(category)
    if title is None or items is None:
        return None
    head = _head(f"Focus: {title}", None)
    notice_html = f"<span class='notice'>{_e(notice)}</span>" if notice else ""
    header = (
        f"<header><h1>{LOGO} / <a href='/focus'>focus</a>"
        f" / {_e(title)}</h1>{notice_html}</header>{alert}<main class='single'>"
    )
    if not items and done is None:
        return (
            head + header + "<article class='panel padded'><h2>Nothing here</h2>"
            "<p>This category is empty. <a href='/focus'>Pick another</a>.</p>"
            "</article></main></body></html>"
        )
    index = max(0, min(index, len(items) - (0 if done else 1)))
    here = focus_url(category, index)
    if done is not None:
        done_panel, item = done
        panel = dashboard.find_panel(done_panel)
        panel_title, description = (panel.title, panel.description) if panel else ("Done", "")
        position = f"done · {len(items) - index} to go"
        next_index = index
    else:
        panel, item = items[index]
        panel_title, description = panel.title, panel.description
        position = f"{index + 1} of {len(items)}"
        next_index = index + 1
    prev_link = (
        f"<a class='button secondary prev' href='{_e(focus_url(category, index - 1))}'>← Prev</a>"
        if index > 0
        else "<span class='button secondary disabled prev'>← Prev</span>"
    )
    next_link = (
        f"<a class='button secondary next' href='{_e(focus_url(category, next_index))}'>Next →</a>"
        if next_index < len(items)
        else "<span class='button secondary disabled next'>Next →</span>"
    )
    return (
        head + header + "<nav class='focus-nav'>"
        f"{prev_link}<span class='focus-position'>{_e(position)}</span>{next_link}"
        "</nav><article class='panel focus'>"
        f"<div class='focus-panel'><h2>{_e(panel_title)}</h2>"
        f"<span class='description'>{_e(description)}</span></div>"
        f"<ul class='items'>{_item(item, here)}</ul></article>"
        f"</main><script>{FOCUS_KEYS_SCRIPT}</script></body></html>"
    )
