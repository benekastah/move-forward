"""Running external commands."""

import sys

import pytest

from move_forward.shell import CommandError, run


def test_a_failing_command_keeps_what_it_printed() -> None:
    script = "import sys; print('so far'); print('broke', file=sys.stderr); sys.exit(3)"
    with pytest.raises(CommandError, match="broke") as failed:
        run([sys.executable, "-c", script])
    assert failed.value.stdout.strip() == "so far"
