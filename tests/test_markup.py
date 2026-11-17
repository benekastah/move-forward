import pytest
from pydantic import BaseModel

from move_forward.markup import Html, html


def test_an_interpolated_value_is_escaped() -> None:
    title = "<script>alert('x')</script> & \"more\""
    assert html(t"<h2 title='{title}'>{title}</h2>") == (
        "<h2 title='&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt; &amp; &quot;more&quot;'>"
        "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt; &amp; &quot;more&quot;</h2>"
    )


def test_what_a_builder_returned_goes_in_as_it_is() -> None:
    inner = html(t"<i>{'a & b'}</i>")
    assert isinstance(inner, Html)
    assert html(t"<b>{inner}</b>") == "<b><i>a &amp; b</i></b>"


def test_text_added_to_html_is_text_again() -> None:
    inner = html(t"<i>x</i>")
    assert html(t"{inner + '<b>'}") == "&lt;i&gt;x&lt;/i&gt;&lt;b&gt;"


def test_a_nested_template_and_a_list_are_built_the_same_way() -> None:
    rows = [t"<li>{n}</li>" for n in ("<a>", "b")]
    optional = t" title='{'<t>'}'" if rows else ""
    assert html(t"<ul{optional}>{rows}</ul>") == (
        "<ul title='&lt;t&gt;'><li>&lt;a&gt;</li><li>b</li></ul>"
    )


def test_a_conversion_and_a_format_spec_apply_before_escaping() -> None:
    said = "it's <ok>"
    assert html(t"confirm({said!r})") == "confirm(&quot;it&#x27;s &lt;ok&gt;&quot;)"
    assert html(t"{3:03d}") == "003"
    assert html(t"{html(t'<b>'):>4}") == " &lt;b&gt;"  # formatted, it's text again


def test_a_generator_or_an_iterator_is_built_item_by_item() -> None:
    items = (t"<li>{n}</li>" for n in ("<a>", "b"))
    assert html(t"<ul>{items}</ul>") == "<ul><li>&lt;a&gt;</li><li>b</li></ul>"
    assert html(t"{map(str.upper, ['<a>', 'b'])}") == "&lt;A&gt;B"


def test_none_is_refused_rather_than_shown_as_none() -> None:
    with pytest.raises(TypeError, match="None"):
        html(t"<p>{None}</p>")


class _Model(BaseModel):
    a: int = 1


@pytest.mark.parametrize("value", [{"a": 1}, {"a"}, frozenset("a"), bytearray(b"a"), _Model()])
def test_an_iterable_that_isnt_a_sequence_of_parts_is_refused(value: object) -> None:
    with pytest.raises(TypeError):
        html(t"{value}")
