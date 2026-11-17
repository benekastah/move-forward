"""Building a plugin's pages and buttons: core's page frame, HTML from t-strings, text as
shown, and buttons. `html(t"<p>{title}</p>")` escapes every value in it unless it's `Html`
(what `html`, `page` or `buttons` returned), so build a page's HTML only through it."""

from move_forward.markup import Html, html
from move_forward.render import buttons, linkable, page
from move_forward.text import shown

__all__ = ["Html", "buttons", "html", "linkable", "page", "shown"]
