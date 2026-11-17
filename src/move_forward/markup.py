"""HTML built from t-strings, escaped by construction: `html(t"<b>{title}</b>")` escapes
`title` unless it's already HTML, so no value reaches the page as markup by being forgotten."""

from collections.abc import Iterable, Iterator
from html import escape
from string.templatelib import Interpolation, Template, convert
from typing import cast


class Html(str):
    """Text that is already HTML, which `html` puts in as it is: what `html` returned. Adding
    text to it makes plain text again (`str`'s operations return a `str`), which `html` escapes.
    Make one by hand only of a constant known to be HTML, such as a page's script."""

    __slots__ = ()


def html(template: Template) -> Html:
    """The HTML of `template`: its own text as it is, and each value in it escaped for HTML
    text and attribute values, but `Html` (put in as it is), a nested t-string (built the same
    way) and a list, tuple or iterator (a generator) of those, each item in turn. None, and
    any other iterable but text (a dict, a set, a model), raise `TypeError`, rather than showing
    "None" or what iterating it happens to give. A conversion (`!r`) and a format spec apply
    first, making text."""
    return Html("".join(_part(part) for part in template))


def _part(part: str | Interpolation[object]) -> str:
    if isinstance(part, str):
        return part
    value = part.value
    if part.conversion or part.format_spec:
        value = format(convert(value, part.conversion), part.format_spec)
    return _value(value)


def _value(value: object) -> str:
    if isinstance(value, Html):
        return value
    if isinstance(value, Template):
        return html(value)
    if value is None:
        raise TypeError('None in HTML: leave the value out, or interpolate ""')
    if isinstance(value, (list, tuple, Iterator)):
        return "".join(_value(v) for v in cast(Iterable[object], value))
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        raise TypeError(f"{type(cast(object, value)).__name__} in HTML: build a list of its parts")
    return escape(str(value), quote=True)
