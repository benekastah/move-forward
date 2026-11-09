"""Text is read and written as UTF-8 everywhere: Windows' default is its code page, which
fails or garbles anything else (an emoji in a PR title, a name with an accent)."""

import ast
import sys
import tomllib
from pathlib import Path

from move_forward import tomledit
from move_forward.shell import run

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [*sorted((ROOT / "src").rglob("*.py")), ROOT / "dev.py"]


def _binary(call: ast.Call) -> bool:
    """An `open`/`fdopen` whose mode says bytes: no encoding to give."""
    modes = [*call.args[1:2], *(k.value for k in call.keywords if k.arg == "mode")]
    return any(isinstance(m, ast.Constant) and "b" in str(m.value) for m in modes)


def _unencoded(source: Path) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            name = "open"  # the builtin; os.open, webbrowser.open and the like aren't text
        elif isinstance(func, ast.Attribute) and func.attr in ("read_text", "write_text", "fdopen"):
            name = func.attr
        else:
            continue
        if name in ("open", "fdopen") and _binary(node):
            continue
        if not any(k.arg == "encoding" for k in node.keywords):
            found.append(f"{source.relative_to(ROOT).as_posix()}:{node.lineno}: {name}")
    return found


def test_every_text_read_and_write_says_its_encoding() -> None:
    assert [line for source in SOURCES for line in _unencoded(source)] == []


def test_a_commands_output_is_read_as_utf_8_and_a_stray_byte_spoils_only_itself() -> None:
    script = "import sys; sys.stdout.buffer.write('PR 🚀 \\u00e9'.encode() + b' \\xff')"
    out = run([sys.executable, "-c", script])
    assert out == "PR 🚀 é �"


def test_text_with_an_emoji_is_written_as_toml_that_reads_back() -> None:
    text = tomledit.set_key("[slack]\n", "slack", "ping_first", "Ready 🚀 for review?")
    assert tomllib.loads(text)["slack"]["ping_first"] == "Ready 🚀 for review?"
