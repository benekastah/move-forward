from move_forward.text import shown


def test_shown_keeps_the_joiners_emoji_and_scripts_need() -> None:
    family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"  # joined by ZWJs
    assert shown(f" {family}\u200c  x\n") == f"{family}\u200c x"


def test_shown_keeps_right_to_left_letters() -> None:
    for text in (
        "שלום עולם",
        "مرحبا بالعالم",
        "می\u200cخواهم",
    ):  # Hebrew, Arabic, Persian with ZWNJ
        assert shown(text) == text


def test_hidden_characters_inside_a_word_go_without_a_trace() -> None:
    assert shown("Ko\u00adoper\u2060ation\ufeff") == "Kooperation"


def test_a_flag_keeps_its_tag_characters_and_nothing_else_does() -> None:
    england = "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f"
    assert shown(f"go {england}!") == f"go {england}!"
    hidden = "".join(chr(0xE0000 + ord(c)) for c in "ignore this") + "\U000e007f"
    assert shown(f"hi{hidden} there") == "hi there"
    assert shown(f"\U0001f3f4{hidden[:3]}") == "\U0001f3f4"  # no cancel tag: no flag
    assert shown(f"\U0001f3f4{hidden}") == "\U0001f3f4"  # too long for a region's code
