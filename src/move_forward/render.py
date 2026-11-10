"""Server-side HTML for the dashboard and focus mode, and the frame of plugins' pages."""

import hashlib
import json
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import datetime
from html import escape

from . import localtime, tag_view
from .act_now import OPENED_FIELD
from .page import Action, Badge, Group, Item, Link, Panel, SourceStatus, age
from .panels import ACT_NOW, Dashboard
from .text import shown

PAGE_RELOAD_SECONDS = 60
REFRESHING_RELOAD_SECONDS = 10
"""Reload faster while a refresh runs, so the new data shows up soon after it lands."""
OPEN_SECTIONS_SCRIPT = (
    # The server renders each <details> in its default state. This restores the state the
    # browser last saw, from localStorage, and keeps it current on each toggle. Only
    # sections it has a state for are touched, so a new panel keeps its default. Browsers
    # fire toggle at load for sections that start open, and for the ones restored here;
    # saving then just writes the same state again. Saving replaces the state with the page's,
    # dropping sections gone for good, but while the tag view leaves some off the page
    # (`data-filtered` on `main`), it keeps theirs too. Without localStorage (a private window,
    # say) the sections stay as rendered. Runs again on `mfd:replaced`, when RELOAD_SCRIPT
    # swaps in a new page: it binds only the new elements, so nothing piles up.
    "const initOpenSections = () => {"
    " const sections = [...document.querySelectorAll('details[data-key]')];"
    " const read = () => {"
    "  try { return JSON.parse(localStorage.getItem('mfd.open') || '{}'); } catch { return {}; } };"
    " const saved = read();"
    " for (const s of sections) if (s.dataset.key in saved) s.open = saved[s.dataset.key];"
    " const kept = () => (document.querySelector('main[data-filtered]') ? read() : {});"
    " const save = () => { try { localStorage.setItem('mfd.open', JSON.stringify({ ...kept(),"
    "  ...Object.fromEntries(sections.map((s) => [s.dataset.key, s.open])) })); }"
    "  catch {} };"
    " for (const s of sections) s.addEventListener('toggle', save);"
    "};"
    "document.addEventListener('DOMContentLoaded', initOpenSections);"
    "document.addEventListener('mfd:replaced', initOpenSections);"
)
"""Restores the open sections from localStorage and keeps them saved there; see inside."""
LAYOUT_SCRIPT = (
    # Dragging a group's or a panel's summary moves it among its siblings (a panel stays in
    # its group, and Act now, which has no data-drag, stays first), then posts the page's
    # whole order to /layout. Without the script the page keeps its order. While the tag view
    # leaves groups or panels off the page, none has data-drag, as their order can't be
    # saved whole. Like
    # OPEN_SECTIONS_SCRIPT, it binds the new elements again on `mfd:replaced`.
    "const initLayout = () => {"
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
    "   document.documentElement.dataset.dragging = '1';"
    "   e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', el.dataset.key); });"
    "  summary.addEventListener('dragend', () => {"
    "   dragged = null; delete document.documentElement.dataset.dragging; });"
    "  const sibling = () => dragged && dragged !== el && dragged.parentElement === el.parentElement;"
    "  el.addEventListener('dragover', (e) => { if (sibling()) e.preventDefault(); });"
    "  el.addEventListener('drop', (e) => {"
    "   if (!sibling()) return;"
    "   e.preventDefault(); e.stopPropagation();"
    "   const box = el.getBoundingClientRect();"
    "   el.parentElement.insertBefore(dragged, e.clientY > box.top + box.height / 2 ? el.nextSibling : el);"
    "   save(); });"
    " }"
    "};"
    "document.addEventListener('DOMContentLoaded', initLayout);"
    "document.addEventListener('mfd:replaced', initLayout);"
)
"""Reorders groups and panels by dragging their summaries, and stores the order; see inside."""
RELOAD_SCRIPT = (
    # Every `data-reload` seconds, fetches the page again in the background and swaps its
    # body in, so the scroll position and the focus stay. The server may be restarting just
    # then: a failed fetch (or one that takes over FETCH_MS) is retried, after 1, 2, 4 and
    # 8 seconds, and when all fail, the next round tries again. One timer is pending at a
    # time, and each fetch has its own AbortController and timeout, cleared when it ends.
    # The swap waits while something is being dragged or a field has the focus. Dashboard action forms
    # use the same swap after their POST redirect, restoring scroll instead of navigating
    # to its anchor. The old elements go with their listeners; the other scripts bind the
    # new ones on `mfd:replaced`.
    # The place an action was pressed (`at`, `pos`) only matters to the page that follows it,
    # so it leaves the address: later reloads don't ask for it.
    "(() => {"
    " const here = new URL(location.href);"
    " if (here.searchParams.has('pos')) {"
    "  here.searchParams.delete('at'); here.searchParams.delete('pos');"
    "  history.replaceState(null, '', here.pathname + here.search + here.hash); }"
    " const FETCH_MS = 15000, RETRIES = 4;"
    " let revision = 0, acting = false;"
    " const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));"
    " const get = async () => {"
    "  const abort = new AbortController();"
    "  const timer = setTimeout(() => abort.abort(), FETCH_MS);"
    "  try {"
    "   const r = await fetch(location.href, { cache: 'no-store', signal: abort.signal,"
    "    headers: { Accept: 'text/html' } });"
    "   if (!r.ok) throw new Error(r.status);"
    "   return new DOMParser().parseFromString(await r.text(), 'text/html');"
    "  } finally { clearTimeout(timer); } };"
    " const fetchPage = async () => {"
    "  for (let i = 0; ; i++) {"
    "   try { return await get(); } catch (err) { if (i === RETRIES) return null; }"
    "   await wait(1000 * 2 ** i); } };"
    " const busy = () => document.documentElement.dataset.dragging"
    "  || document.activeElement?.closest('input, textarea, select');"
    " const swap = (next) => {"
    "  document.title = next.title;"
    "  document.body.dataset.reload = next.body.dataset.reload || '';"
    "  document.body.replaceChildren(...[...next.body.childNodes].map((n) => document.adoptNode(n)));"
    "  document.dispatchEvent(new Event('mfd:replaced')); };"
    " const round = async () => {"
    "  const started = revision;"
    "  const next = await fetchPage();"
    "  if (next && next.body.dataset.reload && !busy() && !acting && started === revision)"
    "   swap(next);"
    "  schedule(); };"
    " const schedule = () => {"
    "  const seconds = Number(document.body.dataset.reload);"
    "  if (seconds) setTimeout(round, seconds * 1000); };"
    " document.addEventListener('submit', async (event) => {"
    "  const form = event.target;"
    "  if (event.defaultPrevented || !form.matches('form.action')"
    "   || !new URL(form.action).pathname.startsWith('/actions/')) return;"
    "  event.preventDefault();"
    "  if (acting) return;"
    "  acting = true; revision++;"
    "  const x = scrollX, y = scrollY;"
    "  try {"
    "   const response = await fetch(form.action, { method: 'POST',"
    "    body: new URLSearchParams(new FormData(form)), cache: 'no-store',"
    "    headers: { Accept: 'text/html' } });"
    "   if (!response.ok || !response.redirected) throw new Error(response.status);"
    "   const next = new DOMParser().parseFromString(await response.text(), 'text/html');"
    "   if (!next.body.dataset.reload) throw new Error('not a dashboard');"
    "   swap(next);"
    "   const current = new URL(location.href); current.hash = '';"
    "   history.replaceState(null, '', current.pathname + current.search);"
    "   scrollTo(x, y);"
    "  } catch (error) {"
    "   alert('The action may have run, but the page could not update. Reload to check.'); }"
    "  finally { acting = false; revision++; } });"
    " document.addEventListener('DOMContentLoaded', schedule);"
    "})();"
)
"""Refreshes and applies dashboard actions in place, retrying background reloads."""
OPENED_SCRIPT = (
    # Opening a link of an item (its PR, its ticket) means I'm about to act on it elsewhere.
    # Its subjects are remembered for this tab (sessionStorage, half an hour), and when I
    # come back to the tab, they're fetched again (`/refresh/subjects`), not whole plugins.
    # An Act now item is also marked opened (`act_now/opened`), dimmed in its place until it
    # changes. An item's ↻ button fetches its subjects again at once. One delegated listener
    # on the document, so it needs no rebinding when RELOAD_SCRIPT swaps the body in; a
    # failed request only means the page shows the change at its next reload. A link in an
    # item's badges (a `#tag`'s, filtering the page) opens nothing.
    "(() => {"
    " const KEY = 'mfd-opened', KEEP = 30 * 60 * 1000;"
    " const kept = () => { try { return JSON.parse(sessionStorage.getItem(KEY) || '[]'); }"
    "  catch { return []; } };"
    " const subjectsOf = (el) => { try { return JSON.parse(el.dataset.subjects || '[]'); }"
    "  catch { return []; } };"
    " const refetch = (subjects) => { if (!subjects.length) return;"
    "  fetch('/refresh/subjects', { method: 'POST', keepalive: true,"
    "   headers: { 'Content-Type': 'application/json' },"
    "   body: JSON.stringify(subjects.slice(0, 50)) }).catch(() => {}); };"
    " document.addEventListener('click', (e) => {"
    "  const again = e.target.closest && e.target.closest('button.refetch');"
    "  if (again) { refetch(subjectsOf(again)); return; }"
    "  const link = e.target.closest && e.target.closest('li.item a[href]');"
    "  if (!link || link.closest('form, .badges')) return;"
    "  const item = link.closest('li.item'), now = Date.now();"
    "  const remembered = kept().filter((s) => now - s.at < KEEP);"
    "  for (const s of subjectsOf(item)) remembered.push({ kind: s.kind, id: s.id, at: now });"
    "  sessionStorage.setItem(KEY, JSON.stringify(remembered));"
    "  if (item.dataset.opened) {"
    "   fetch('/actions/act_now/opened', { method: 'POST', redirect: 'manual', keepalive: true,"
    "    body: new URLSearchParams(JSON.parse(item.dataset.opened)) }).catch(() => {});"
    "   item.classList.add('opened'); } });"
    " window.addEventListener('focus', () => {"
    "  const now = Date.now(), seen = new Set(), subjects = [];"
    "  for (const s of kept()) { const key = s.kind + ' ' + s.id;"
    "   if (now - s.at < KEEP && !seen.has(key)) { seen.add(key);"
    "    subjects.push({ kind: s.kind, id: s.id }); } }"
    "  refetch(subjects); });"
    "})();"
)
"""Fetches again what I opened when I come back to the tab, and dims an opened Act now item;
see inside."""
REFRESH_ICON_SCRIPT = (
    # Three valid favicon frames cycle while refreshing. A background tab may throttle this
    # timer, so each frame stands on its own. Replace the link because Firefox may ignore
    # an href change on an existing favicon link.
    "(() => {"
    " const frames = ['/favicon-refresh.svg', '/favicon-refresh-1.svg',"
    "  '/favicon-refresh-2.svg'];"
    " let timer = null, frame = 0;"
    " const show = (href) => {"
    "  const old = document.querySelector('link[rel=icon]');"
    "  if (!old || old.getAttribute('href') === href) return;"
    "  const icon = document.createElement('link');"
    "  icon.rel = 'icon'; icon.type = 'image/svg+xml'; icon.href = href;"
    "  old.replaceWith(icon); };"
    " const check = () => {"
    "  if (document.querySelector('[data-refreshing]')) {"
    "   if (!timer) { frame = 0; show(frames[frame]);"
    "    timer = setInterval(() => { frame = (frame + 1) % frames.length;"
    "     show(frames[frame]); }, 400); }"
    "  } else {"
    "   if (timer) clearInterval(timer);"
    "   timer = null; frame = 0; show('/favicon.svg'); } };"
    " document.addEventListener('DOMContentLoaded', check);"
    " document.addEventListener('mfd:replaced', check);"
    "})();"
)
"""Highlights each dot of the refresh favicon in turn; see inside."""
NOTIFY_SCRIPT = (
    # The server sends events over a live connection, avoiding browser timers throttled in
    # hidden tabs. Only one tab holds it: each holds a connection for good, and over HTTP/1
    # a browser has about six per host, so a few tabs would leave none for pages and
    # buttons. The tabs queue for a lock; the one holding it listens, and when it closes (or
    # its stream does for good) the next takes over. Older browsers fall back to polling. Without permission neither takes
    # events. A notification's `tag` replaces one still showing for the same thing.
    "(() => {"
    " const NOTIFY_SECONDS = 15;"
    " const show = (items) => { for (const n of items) {"
    "  const note = new Notification(n.title, { body: n.body, tag: n.tag });"
    "  note.onclick = () => { window.focus(); location.assign(n.url); note.close(); };"
    " } };"
    " const poll = async () => {"
    "  if (!('Notification' in window) || Notification.permission !== 'granted') return;"
    "  try {"
    "   const r = await fetch('/notifications', { method: 'POST', redirect: 'manual' });"
    "   if (!r.ok) return;"
    "   show(await r.json());"
    "  } catch (err) { /* the server may be restarting; the next poll tries again */ } };"
    " document.addEventListener('DOMContentLoaded', () => {"
    "  if ('Notification' in window && Notification.permission === 'granted'"
    "   && 'EventSource' in window) {"
    "   const listen = () => new Promise((done) => {"
    "    const source = new EventSource('/notifications/stream');"
    "    source.onmessage = (event) => { try { show(JSON.parse(event.data)); } catch {} };"
    # Closed for good (refused: signed out, say): let another tab listen, and queue again.
    "    source.onerror = () => { if (source.readyState === EventSource.CLOSED) {"
    "     done(); setTimeout(hold, 60000); } }; });"
    "   const hold = () => navigator.locks"
    "    ? navigator.locks.request('mfd-notifications', listen) : listen();"
    "   hold();"
    "  } else { poll(); setInterval(poll, NOTIFY_SECONDS * 1000); } });"
    "})();"
)
"""Tells me of the events waiting on the server, as browser notifications; see inside."""
FOCUS_KEYS_SCRIPT = (
    "document.addEventListener('keydown', (e) => {"
    " if (e.target.closest('input, textarea, select')) return;"
    " const link = document.querySelector(e.key === 'ArrowLeft' ? 'a.prev' : e.key === 'ArrowRight'"
    " ? 'a.next' : 'nothing'); if (link) link.click(); });"
)
"""Focus mode: the left and right arrow keys go to the previous and next item."""
LOGO = "<a href='/' class='logo'><img src='/logo.svg' alt=''> Move forward</a>"
"""The header's home link: the arrow (the favicon without its circle) and the name."""


def _logo(tags: Sequence[str]) -> str:
    """`LOGO`, keeping the tags the page is filtered by."""
    return LOGO.replace("href='/'", f"href='{_e(tag_view.url('/', tags))}'")


@dataclass(frozen=True)
class _Where:
    """The page items are shown on, and the tags it's filtered by (see `tag_view`): where
    their tag badges link to, adding theirs, and what their buttons come back to."""

    path: str = "/"
    params: tuple[tuple[str, str], ...] = ()
    tags: tuple[str, ...] = ()

    def url(self, tags: Sequence[str]) -> str:
        """This page filtered by `tags`."""
        return tag_view.url(self.path, tags, dict(self.params))


_UNFILTERED = _Where()
"""The dashboard, not filtered by any tag."""


def _e(text: object) -> str:
    return escape(str(text), quote=True)


def _shown(text: str) -> str:
    """`text` as shown, escaped."""
    return _e(shown(text))


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


def _head(title: str, reload_seconds: int | None, refresh_icon: bool = False) -> str:
    """Up to and including the `<body>` tag.

    With `reload_seconds`, the page fetches itself again from JavaScript and swaps the new
    body in (see `RELOAD_SCRIPT`); `<noscript>` falls back to a meta refresh.
    """
    if reload_seconds:
        reload = (
            f'<noscript><meta http-equiv="refresh" content="{reload_seconds}"></noscript>'
            f"<script>{OPEN_SECTIONS_SCRIPT}</script><script>{LAYOUT_SCRIPT}</script>"
            f"<script>{RELOAD_SCRIPT}</script><script>{OPENED_SCRIPT}</script>"
            f"<script>{REFRESH_ICON_SCRIPT}</script><script>{NOTIFY_SCRIPT}</script>"
        )
        body = f"<body data-reload='{reload_seconds}'>"
    else:
        reload = ""
        body = "<body>"
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{_e(title)}</title>{reload}"
        f"<link rel='icon' href='/{'favicon-refresh.svg' if refresh_icon else 'favicon.svg'}'"
        " type='image/svg+xml'>"
        f"<link rel='stylesheet' href='/style.css'></head>{body}"
    )


def local_path(url: str) -> bool:
    """Whether `url` is a path on this dashboard, and nothing a browser would take for another
    site: not `//host`, nor `/\\host` (browsers read a backslash as a slash), and nothing
    unprintable (which could split a redirect's header)."""
    return (
        url.startswith("/") and not url.startswith("//") and "\\" not in url and url.isprintable()
    )


def linkable(url: str | None) -> bool:
    """Whether `url` may be a link: http(s), or a path on this dashboard. Anything else (a
    `javascript:` URL, say, should one ever reach a plugin's data) is shown as text."""
    return url is not None and (url.startswith(("https://", "http://")) or local_path(url))


def _badge(badge: str | Badge, href: str | None = None) -> str:
    """A badge, coloured by its tone; a link to `href`, when given (a `#tag`'s, filtering the
    page by it)."""
    text, tone = (badge, "") if isinstance(badge, str) else (badge.text, badge.tone)
    tone = "" if tone in ("", "plain") else f" {tone}"
    if href is None:
        return f"<span class='badge{tone}'>{_shown(text)}</span>"
    return (
        f"<a class='badge tag{tone}' href='{_e(href)}'"
        f" title='Show only what carries {_e(text)}'>{_shown(text)}</a>"
    )


def _badges(item: Item, where: _Where | None) -> str:
    """An item's badges; on the dashboard or focus mode (`where`), each of its tags'
    (`Item.tags`) links to the page filtered by it too."""

    def href(badge: str | Badge) -> str | None:
        text = badge if isinstance(badge, str) else badge.text
        tag = text.removeprefix("#")
        if where is None or not text.startswith("#") or tag not in item.tags:
            return None
        return where.url(tag_view.adding(where.tags, tag))

    return "".join(_badge(b, href(b)) for b in item.badges)


def _link_action(action: Action) -> str:
    if not linkable(action.href):
        return ""
    title = f" title='{_e(action.title)}'" if action.title else ""
    return (
        f"<a class='button link-action{' muted' if action.muted else ''}'"
        f" href='{_e(action.href)}'{title}>{_e(action.label)}</a>"
    )


def _text_field(action: Action, at: str, pos: int | None) -> str:
    """A button's text field, with its suggestions in a `<datalist>`. The list's id comes from
    the button and its place, so the same item shown in two panels has one each."""
    placeholder = _e(action.input or "")
    if not action.suggestions:
        return f"<input type='text' name='input' placeholder='{placeholder}' required>"
    seen = json.dumps([action.name, action.fields, action.suggestions, at, pos], sort_keys=True)
    list_id = f"suggestions-{hashlib.sha256(seen.encode()).hexdigest()[:16]}"
    options = "".join(f"<option value='{_e(s)}'></option>" for s in action.suggestions)
    return (
        f"<input type='text' name='input' placeholder='{placeholder}' list='{list_id}'"
        f" autocomplete='off' required><datalist id='{list_id}'>{options}</datalist>"
    )


def _choices(action: Action) -> str:
    """A button's drop-down, or nothing without one."""
    if not action.choices:
        return ""
    options = "".join(
        f"<option value='{_e(value)}'>{_e(label)}</option>"
        for label, value in action.choices.items()
    )
    return f"<select name='choice' aria-label='{_e(action.label)}'>{options}</select>"


def _action(
    action: Action,
    back: str = "",
    at: str = "",
    pos: int | None = None,
    tags: Sequence[str] = (),
) -> str:
    """A button's form. `back` is where the action redirects afterwards; default the
    dashboard, filtered by `tags`. `at` is the key of the panel it's in, and `pos` its item's
    place there: the redirect returns to that place (see `Spot`), not the top of the page."""
    fields = "".join(
        f"<input type='hidden' name='{_e(name)}' value='{_e(value)}'>"
        for name, value in action.fields.items()
    )
    confirm = f" onsubmit='return confirm({_e(repr(action.confirm))})'" if action.confirm else ""
    params = {k: v for k, v in (("back", back), ("at", at)) if v}
    if at and pos is not None:
        params["pos"] = str(pos)
    query = tag_view.url("", () if back else tags, params)
    if action.checkbox:
        control = (
            f"<label class='check{' suggested' if action.suggested else ''}'>"
            f"<input type='checkbox'{' checked' if action.checked else ''}"
            f" onchange='this.form.requestSubmit()'> {_e(action.label)}</label>"
        )
    else:
        selected = " selected" if action.selected else ""
        pressed = (
            f" aria-pressed='{str(action.selected).lower()}'" if action.selected is not None else ""
        )
        title = (
            f" title='{_e(action.label)}' aria-label='{_e(action.label)}'" if action.icon else ""
        )
        label = (
            f"<span aria-hidden='true'>{_e(action.icon)}</span>"
            if action.icon
            else _e(action.label)
        )
        control = (
            f"<button type='submit' class='secondary small{selected}'{pressed}{title}>"
            f"{label}</button>"
        )
        field = _text_field(action, at, pos) if action.input else ""
        control = field + _choices(action) + control
    return (
        f"<form method='post' action='/actions/{_e(action.name)}{query}' class='action'{confirm}>"
        f"{fields}{control}</form>"
    )


def buttons(actions: list[Action], back: str = "", at: str = "", pos: int | None = None) -> str:
    """Buttons for `actions` (those that post, not links), each asking first if it has a
    `confirm`, for plugins' pages as for the dashboard. `back` is where each redirects
    afterwards; default the page they're on."""
    return _buttons(actions, back, at, pos)


def _buttons(
    actions: list[Action],
    back: str = "",
    at: str = "",
    pos: int | None = None,
    tags: Sequence[str] = (),
) -> str:
    """`buttons`, coming back to the dashboard filtered by `tags` (see `tag_view`) unless
    `back` says where."""
    # A plugin may add an action after core's own groups. Keep the first position of
    # each named group and collect its later actions there as well.
    sequence: list[str | Action] = []
    groups: dict[str, list[Action]] = {}
    for action in actions:
        if action.href is not None:
            continue
        if action.group is None:
            sequence.append(action)
        else:
            if action.group not in groups:
                sequence.append(action.group)
                groups[action.group] = []
            groups[action.group].append(action)
    forms = ""
    for choice in sequence:
        if isinstance(choice, Action):
            forms += _action(choice, back, at, pos, tags)
        else:
            rendered = "".join(_action(action, back, at, pos, tags) for action in groups[choice])
            forms += (
                f"<div class='button-group' role='group' aria-label='{_e(choice)}'>{rendered}</div>"
            )
    return f"<div class='actions'>{forms}</div>" if actions else ""


def _link(link: Link) -> str:
    """A link; one to what's over is struck through."""
    classes = [c for c, on in (("strong", link.strong), ("done", link.done)) if on]
    cls = f" class='{' '.join(classes)}'" if classes else ""
    if not linkable(link.url):
        return f"<span{cls}>{_shown(link.text)}</span>"
    return f"<a href='{_e(link.url)}'{cls} target='_blank' rel='noopener'>{_shown(link.text)}</a>"


def item_html(item: Item) -> str:
    """An item's HTML, as a panel on the page shows it."""
    return _item(item)


def _without_shared(part: Item, item: Item) -> Item:
    """A part as listed beneath its item, without the buttons the item has anyway. Act now's
    mark of opening an item doesn't make a button another."""

    def key(action: Action) -> tuple[str, dict[str, str]]:
        return action.name, {k: v for k, v in action.fields.items() if k != OPENED_FIELD}

    shared = [key(action) for action in item.actions]
    return part.model_copy(update={"actions": [a for a in part.actions if key(a) not in shared]})


def _item(
    item: Item,
    back: str = "",
    at: str = "",
    pos: int | None = None,
    children: str = "",
    child_count: int = 0,
    where: _Where | None = None,
) -> str:
    """An item's HTML. On the dashboard or focus mode (`where`), its tags' badges link to the
    page filtered by them, and its buttons keep the page's filter."""
    title = (
        f"<a href='{_e(item.url)}' target='_blank' rel='noopener'>{_shown(item.title)}</a>"
        if linkable(item.url)
        else _shown(item.title)
    )
    anchor = f" id='{_e(item.anchor)}'" if item.anchor else ""
    data = f" data-opened='{_e(item.act_now_fields)}'" if item.act_now_fields else ""
    subjects = json.dumps([{"kind": s.kind, "id": s.id} for s in item.subjects])
    data += f" data-subjects='{_e(subjects)}'" if item.subjects else ""
    refetch = (
        f"<button type='button' class='button link-action secondary refetch' data-subjects='{_e(subjects)}'"
        " title='Fetch it again now'>↻</button>"
        if item.subjects
        else ""
    )
    cls = "item opened" if item.opened_at or item.set_aside or item.done else "item"
    parts = [
        f"<li class='{cls}'{anchor}{data}><div class='item-head'>",
        f"<div class='item-title'>{title}</div>"
        + "".join(_link_action(a) for a in item.actions if a.href is not None)
        + refetch
        + "</div>",
    ]
    if item.subtitle:
        parts.append(f"<div class='item-subtitle'>{_shown(item.subtitle)}</div>")
    if item.badges:
        parts.append(f"<div class='badges'>{_badges(item, where)}</div>")
    # One part is drawn only when it's marked listed: an entry with one todo shows it as
    # itself, but one joined to another item in Act now is listed there.
    show_parts = len(item.parts) > 1 or (item.parts_listed and bool(item.parts))
    if item.details:
        parts.append(
            "<ul class='details'>"
            + "".join(f"<li>{_shown(d)}</li>" for d in item.details)
            + "</ul>"
        )
    if show_parts:
        parts.append(
            "<ul class='item-parts'>"
            + "".join(
                _item(_without_shared(part, item), back, at, pos, where=where)
                for part in item.parts
            )
            + "</ul>"
        )
    if item.links:
        links = "".join(_link(link) for link in item.links)
        parts.append(f"<div class='links'>{links}</div>")
    parts.append(_buttons(item.actions, back, at, pos, where.tags if where else ()))
    if children:
        key = f"children:{item.anchor}"
        parts.append(
            f"<details class='item-children' data-key='{_e(key)}'{_open(item.children_open)}>"
            f"<summary>{_e(item.children_title)} ({child_count})</summary>"
            f"<ul class='items'>{children}</ul></details>"
        )
    parts.append("</li>")
    return "".join(parts)


def _open(default: bool) -> str:
    """The `open` attribute, for a section open by default."""
    return " open" if default else ""


Spot = tuple[str, int]
"""Where an action was pressed: a panel's key, and the place of its item there. The page that
follows has an empty anchor, `SPOT_ID`, at that place (and only that page), so the browser
scrolls to where the item was, even once it's gone from the end of its panel."""
SPOT_ID = "spot"


def _nesting(items: list[Item]) -> dict[int | None, list[tuple[int, Item]]]:
    """Each item's children by its place (None for the roots), from their `parent_anchor`: a
    child goes under the first item with that anchor, so it's drawn once, and an item whose
    parents lead back to it (a cycle) is a root, so it's drawn at all."""
    owner: dict[str, int] = {}
    for index, item in enumerate(items):
        if item.anchor:
            owner.setdefault(item.anchor, index)

    def parent(index: int) -> int | None:
        found = owner.get(items[index].parent_anchor or "")
        return found if found != index else None

    def cycles(index: int) -> bool:
        """Whether `index` is in a loop of parents (not merely below one)."""
        seen: set[int] = set()
        at = parent(index)
        while at is not None and at not in seen:
            if at == index:
                return True
            seen.add(at)
            at = parent(at)
        return False

    children: dict[int | None, list[tuple[int, Item]]] = {}
    for index, item in enumerate(items):
        up = None if cycles(index) else parent(index)
        children.setdefault(up, []).append((index, item))
    return children


def _live(panel: Panel) -> int:
    """How many of the panel's items there are to do: not what's set aside, or done."""
    return sum(not item.set_aside and not item.done for item in panel.items)


def _panel(panel: Panel, spot: Spot | None = None, where: _Where = _UNFILTERED) -> str:
    open_attr = _open(bool(panel.items))
    indexed = list(enumerate(panel.items))
    children = _nesting(panel.items)

    def cell(index: int, item: Item) -> str:
        descendants = children.get(index, [])
        nested = "".join(cell(n, child) for n, child in descendants)
        marker = (
            f"<li id='{SPOT_ID}' aria-hidden='true'></li>" if spot == (panel.key, index) else ""
        )
        return marker + _item(
            item,
            at=panel.key,
            pos=index,
            children=nested,
            child_count=len(descendants),
            where=where,
        )

    items = "".join(cell(index, item) for index, item in children.get(None, []))  # roots
    if spot and spot[0] == panel.key and spot[1] >= len(indexed):
        items += f"<li id='{SPOT_ID}' aria-hidden='true'></li>"
    return (
        f"<details class='panel{'' if panel.items else ' empty'}' id='{_e(panel.key)}'"
        f" data-key='{_e(panel.key)}'"
        f"{'' if where.tags else ' data-drag'}{open_attr}>"
        f"<summary><h2>{_e(panel.title)}</h2><span class='count'>{_live(panel)}</span>"
        f"<span class='description'>{_e(panel.description)}</span></summary>"
        f"{_buttons(panel.actions, at=panel.key, tags=where.tags)}"
        f"<ul class='items'>{items}</ul></details>"
    )


def _when(now: datetime, then: datetime) -> str:
    """`then` in local time: the time of day if it was on `now`'s day, else the date too."""
    local = localtime.local(then)
    if local.date() == localtime.local(now).date():
        return f"{local:%H:%M}"
    return f"{local:%b} {local.day} {local:%H:%M}"


def _refreshed(
    name: str,
    status: SourceStatus,
    now: datetime,
    refreshing: AbstractSet[str],
    tags: Sequence[str] = (),
) -> str:
    """When a plugin last refreshed (or that it's refreshing, or why its last refresh
    failed), and a button to refresh it now, for its group's summary, back to the page
    filtered by `tags`."""
    if name in refreshing:
        text, cls, title = "refreshing…", "refreshed", ""
    elif status.error:
        text, cls, title = "refresh failed", "refreshed error", status.error
    else:
        text = f"refreshed {_when(now, status.fetched_at)}" if status.fetched_at else "not yet"
        cls, title = "refreshed", f"{age(now, status.fetched_at)} ago" if status.fetched_at else ""
    return (
        f"<span class='{cls}' title='{_e(title)}'>{_e(text)}</span>"
        f"<form method='post' action='{_e(tag_view.url('/refresh', tags))}' class='refresh'>"
        f"<input type='hidden' name='plugin' value='{_e(name)}'>"
        f"<button type='submit' class='small secondary' title='Refresh {_e(name)} now'"
        f"{' disabled' if refreshing else ''}>Refresh</button></form>"
    )


def _plugin_of(name: str) -> str:
    """The plugin a source belongs to: `github.derived` is part of `github`'s refresh, kept as
    its own source (`plugin.collect_into`), and a plugin's own source is just its name."""
    return name.partition(".")[0]


def _status_of(key: str, sources: dict[str, SourceStatus]) -> SourceStatus | None:
    """The plugin `key`'s status: its own source's, but with the error of a part of its
    refresh (e.g. `github.derived`) when that failed and the plugin's own didn't."""
    status = sources.get(key)
    if status is None or status.error:
        return status
    for name, part in sources.items():
        if name != key and _plugin_of(name) == key and part.error:
            return status.model_copy(update={"error": f"{name}: {part.error}"})
    return status


def _group(
    group: Group,
    dashboard: Dashboard,
    refreshing: AbstractSet[str],
    where: _Where,
    spot: Spot | None = None,
) -> str:
    """A group section; panel keys the dashboard left out (e.g. Stacks without git stack) are
    skipped. A plugin's group says when the plugin last refreshed, and refreshes it on a click,
    and how many problems it reports (`Context.problem`), each said above its panels."""
    panels = [p for key in group.panel_keys if (p := dashboard.find_panel(key))]
    count = sum(_live(panel) for panel in panels)
    status = _status_of(group.key, dashboard.sources)
    refreshed = (
        _refreshed(group.key, status, dashboard.generated_at, refreshing, where.tags)
        if status
        else ""
    )
    problems = dashboard.plugin_problems.get(group.key, [])
    counted = said = ""
    if problems:
        counted = (
            f"<span class='problems' title='{_e('; '.join(problems))}'>"
            f"{len(problems)} problem{'' if len(problems) == 1 else 's'}</span>"
        )
        said = (
            "<div class='problems'>"
            + "".join(f"<p class='notice warn'>{_e(problem)}</p>" for problem in problems)
            + "</div>"
        )
    return (
        f"<details class='group' data-key='{_e(group.key)}'"
        + (" data-drag" if group.key != ACT_NOW and not where.tags else "")
        + f"{_open(group.open)}>"
        f"<summary><h2>{_e(group.title)}</h2><span class='count'>{count}</span>{counted}"
        f"{refreshed}</summary>{said}"
        "<div class='panels'>"
        + "".join(_panel(panel, spot, where) for panel in panels)
        + "</div></details>"
    )


def _tag_bar(options: list[tuple[str, int]], where: _Where) -> str:
    """The tag view's controls (see `tag_view`): a drop-down adding a tag the page's items carry
    (with how many do), each selected tag with a link clearing it, and one clearing them all.
    A GET form and links, so it works without scripts; with them, the drop-down sends itself."""
    if not options and not where.tags:
        return ""
    kept = [*where.params, *((tag_view.PARAM, tag) for tag in where.tags)]
    hidden = "".join(
        f"<input type='hidden' name='{_e(name)}' value='{_e(value)}'>" for name, value in kept
    )
    choices = "".join(
        f"<option value='{_e(tag)}'>#{_e(tag)} ({count})</option>"
        for tag, count in options
        if tag not in where.tags
    )
    select = (
        f"<select name='{tag_view.PARAM}' aria-label='Show only what carries a tag'"
        " onchange='this.form.submit()'><option value=''>Filter by tag…</option>"
        f"{choices}</select>"
        "<noscript><button type='submit' class='small secondary'>Add</button></noscript>"
        if choices
        else ""
    )
    selected = "".join(
        f"<a class='badge tag selected' href='{_e(where.url(tag_view.without(where.tags, tag)))}'"
        f" title='Clear #{_e(tag)}' aria-label='Clear #{_e(tag)}'>#{_e(tag)} ×</a>"
        for tag in where.tags
    )
    clear = f"<a class='clear' href='{_e(where.url(()))}'>Clear all</a>" if where.tags else ""
    return (
        f"<nav class='tag-view'><form method='get' action='{_e(where.path)}'>"
        f"{hidden}{select}{selected}{clear}</form></nav>"
    )


def _nothing_carries(where: _Where) -> str:
    """What the page says when nothing on it carries a tag it's filtered by."""
    named = " or ".join(f"#{_e(tag)}" for tag in where.tags)
    return (
        "<article class='panel padded'><h2>Nothing here</h2>"
        f"<p>Nothing carries {named}. <a href='{_e(where.url(()))}'>Clear the filter</a>.</p>"
        "</article>"
    )


def _sources(
    dashboard: Dashboard, shown: Dashboard, refreshing: AbstractSet[str], tags: Sequence[str]
) -> str:
    """The footer: how fresh the data of plugins without a group of their own is, and the
    problems they report (the others' summaries say both), who I am, and the installed plugins
    left out. A plugin whose group the tag view leaves off (`shown`, filtered by `tags`) has its
    summary's refresh status, Refresh button and problems here instead."""
    grouped = {group.key for group in dashboard.groups}
    visible = {group.key for group in shown.groups}
    spans: list[str] = []
    for group in dashboard.groups:
        status = _status_of(group.key, dashboard.sources)
        problems = dashboard.plugin_problems.get(group.key, [])
        if group.key in visible or (status is None and not problems):
            continue
        refreshed = (
            ""
            if status is None
            else _refreshed(group.key, status, dashboard.generated_at, refreshing, tags)
        )
        said = "".join(f" <span class='error'>{_e(problem)}</span>" for problem in problems)
        spans.append(
            f"<span class='left-off'><strong>{_e(group.title)}:</strong>{refreshed}{said}</span>"
        )
    for name, status in dashboard.sources.items():
        if name in grouped or _plugin_of(name) in grouped:
            continue
        if status.error:
            spans.append(
                f"<span class='error'><strong>{_e(name)}:</strong> error ({_e(status.error)})</span>"
            )
        else:
            spans.append(
                f"<span><strong>{_e(name)}:</strong>"
                f" {age(dashboard.generated_at, status.fetched_at)} ago</span>"
            )
    for name, problems in dashboard.plugin_problems.items():
        if name not in grouped:  # a group's, shown or left off, are said with it
            spans += [
                f"<span class='error'><strong>{_e(name)}:</strong> {_e(problem)}</span>"
                for problem in problems
            ]
    if dashboard.identities:
        who = " · ".join(f"{_e(i.tool)}: {_e(i.login)}" for i in dashboard.identities)
        spans.append(f"<span>as {who}</span>")
    spans += [f"<span class='error'>{_e(problem)}</span>" for problem in dashboard.problems]
    return "".join(spans)


def dashboard_page(
    dashboard: Dashboard,
    refreshing: AbstractSet[str],
    notice: str = "",
    spot: Spot | None = None,
    tags: Sequence[str] = (),
) -> str:
    """The page. `spot` is where an action was just pressed (see `Spot`).

    `refreshing` are the plugins a refresh running now collects. `notice` is the outcome of
    an action just run, shown in the header. `tags` are those the page is filtered by (see
    `tag_view`): `dashboard` is built with them selected (`panels.build`'s `selected`).
    """
    where = _Where(tags=tuple(tags))
    shown = tag_view.filtered(dashboard, tags)
    status = (
        "refreshing…" if refreshing else f"built {localtime.local(dashboard.generated_at):%H:%M}"
    )
    reload_seconds = REFRESHING_RELOAD_SECONDS if refreshing else PAGE_RELOAD_SECONDS
    notice_html = f"<span class='notice'>{_e(notice)}</span>" if notice else ""
    return (
        _head("Move forward", reload_seconds, refresh_icon=bool(refreshing))
        + f"<header><h1>{_logo(tags)}</h1><div class='header-right'>"
        f"{notice_html}<span{' data-refreshing' if refreshing else ''}>{_e(status)}</span>"
        f"<a class='button' href='{_e(tag_view.url('/focus', tags))}'>Focus</a>"
        "<a class='button secondary' href='/settings'>Settings</a>"
        f"<form method='post' action='{_e(tag_view.url('/refresh', tags))}'><button type='submit'"
        f"{' disabled' if refreshing else ''}>Refresh</button></form></div></header>"
        + _tag_bar(tag_view.options(dashboard), where)
        + f"<main{' data-filtered' if tags else ''}>"
        + (
            "".join(_group(group, shown, refreshing, where, spot) for group in shown.groups)
            or (_nothing_carries(where) if tags else "")
        )
        + "</main>"
        f"<footer>{_sources(dashboard, shown, refreshing, tags)}"
        f"<form method='post' action='{_e(tag_view.url('/layout/reset', tags))}'>"
        "<button type='submit' class='secondary'>Reset layout</button></form></footer>"
        "</body></html>"
    )


def focus_url(category: str, index: int, tags: Sequence[str] = ()) -> str:
    """Focus mode's page of a category's item at `index`, filtered by `tags`."""
    return tag_view.url("/focus", tags, {"cat": category, "i": str(index)})


def _focus_category(
    dashboard: Dashboard, key: str, title: str, cls: str, tags: Sequence[str]
) -> str:
    """A row of focus mode's first page: the category's title, linked when it has items,
    and how many."""
    count = len(dashboard.focus_items(key) or [])
    if not count:
        return f"<li class='{cls} empty'><span>{_e(title)}</span><span class='count'>0</span></li>"
    return (
        f"<li class='{cls}'><a href='{_e(focus_url(key, 0, tags))}'>{_e(title)}</a>"
        f"<span class='count'>{count}</span></li>"
    )


def focus_categories_page(dashboard: Dashboard, tags: Sequence[str] = ()) -> str:
    """Focus mode's first page: Act now and its panels, then each group's title with its
    panels, each to walk with a count; with `tags`, only what carries one (see `tag_view`
    and `dashboard_page`)."""
    where = _Where("/focus", (), tuple(tags))
    shown = tag_view.focused(dashboard, tags)
    rows: list[str] = []
    for group in shown.groups:
        rows.append(
            _focus_category(shown, group.key, group.title, "category", tags)
            if group.key == ACT_NOW
            else f"<li class='category heading'><span>{_e(group.title)}</span></li>"
        )
        rows.extend(
            _focus_category(shown, panel.key, panel.title, "category sub", tags)
            for key in group.panel_keys
            if (panel := shown.find_panel(key))
        )
    body = (
        "<article class='panel padded'><h2>What to focus on?</h2>"
        "<p>One item at a time, in the skill's order of priority.</p>"
        f"<ul class='categories'>{''.join(rows)}</ul></article>"
        if rows or not tags
        else _nothing_carries(where)
    )
    return (
        _head("Focus", None)
        + f"<header><h1>{_logo(tags)} / focus</h1></header>"
        + _tag_bar(tag_view.options(dashboard), where)
        + f"<main class='single'>{body}</main></body></html>"
    )


def focus_page(
    dashboard: Dashboard,
    category: str,
    index: int,
    notice: str = "",
    done: tuple[str, Item] | None = None,
    tags: Sequence[str] = (),
) -> str | None:
    """One item of a focus category with previous and next links; None for an unknown category.

    An index past the end (the list shrank since the link was made) shows the last item.
    `notice` is the outcome of an action just run from this page. `done` is an item an action
    just changed, e.g. a todo ticked off, with its panel's key: it's shown in place of the
    item at `index` (which is now the one after it), so the change can be undone. With `tags`,
    it steps through only what carries one (see `tag_view` and `dashboard_page`).
    """
    where = _Where("/focus", (("cat", category),), tuple(tags))
    shown = tag_view.focused(dashboard, tags)
    title = shown.focus_title(category)
    items = shown.focus_items(category)
    if title is None or items is None:
        return None
    head = _head(f"Focus: {title}", None)
    notice_html = f"<span class='notice'>{_e(notice)}</span>" if notice else ""
    header = (
        f"<header><h1>{_logo(tags)} / <a href='{_e(tag_view.url('/focus', tags))}'>focus</a>"
        f" / {_e(title)}</h1>{notice_html}</header>"
        + _tag_bar(tag_view.options(dashboard), where)
        + "<main class='single'>"
    )
    if not items and done is None:
        empty = (
            _nothing_carries(where)
            if tags
            else "<article class='panel padded'><h2>Nothing here</h2>"
            "<p>This category is empty. <a href='/focus'>Pick another</a>.</p></article>"
        )
        return head + header + empty + "</main></body></html>"
    index = max(0, min(index, len(items) - (0 if done else 1)))
    here = focus_url(category, index, tags)
    if done is not None:
        done_panel, item = done
        panel = shown.find_panel(done_panel)
        panel_title, description = (panel.title, panel.description) if panel else ("Done", "")
        position = f"done · {len(items) - index} to go"
        next_index = index
    else:
        panel, item = items[index]
        panel_title, description = panel.title, panel.description
        position = f"{index + 1} of {len(items)}"
        next_index = index + 1
    prev_link = (
        f"<a class='button secondary prev' href='{_e(focus_url(category, index - 1, tags))}'>"
        "← Prev</a>"
        if index > 0
        else "<span class='button secondary disabled prev'>← Prev</span>"
    )
    next_link = (
        f"<a class='button secondary next' href='{_e(focus_url(category, next_index, tags))}'>"
        "Next →</a>"
        if next_index < len(items)
        else "<span class='button secondary disabled next'>Next →</span>"
    )
    return (
        head + header + "<nav class='focus-nav'>"
        f"{prev_link}<span class='focus-position'>{_e(position)}</span>{next_link}"
        "</nav><article class='panel focus'>"
        f"<div class='focus-panel'><h2>{_e(panel_title)}</h2>"
        f"<span class='description'>{_e(description)}</span></div>"
        f"<ul class='items'>{_item(item, here, where=where)}</ul></article>"
        f"</main><script>{FOCUS_KEYS_SCRIPT}</script></body></html>"
    )
