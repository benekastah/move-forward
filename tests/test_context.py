"""What a plugin's context gives it: its name, a logger, a directory of its own, and kv."""

import sys
from pathlib import Path

import pytest
from conftest import NOW

from move_forward import private
from move_forward.config import Config
from move_forward.db import Database
from move_forward.domain import Model
from move_forward.registry import enabled, migrate
from move_forward.repositories import KvRepository


class _Seen(Model):
    login: str
    count: int


def test_kv_keeps_json_deletes_and_lists_keys() -> None:
    db = Database(":memory:")
    kv = KvRepository(db, "github")
    assert kv.get_json("seen", _Seen) is None
    kv.set_json("seen", _Seen(login="alice", count=2))
    kv.set_json("prs", ["acme/w#1", "acme/w#2"])
    assert kv.get_json("seen", _Seen) == _Seen(login="alice", count=2)
    assert kv.get_json("prs", list[str]) == ["acme/w#1", "acme/w#2"]
    kv.set("pings:alice", "1")
    assert kv.keys("p") == ["pings:alice", "prs"] and kv.keys() == ["pings:alice", "prs", "seen"]
    kv.delete("prs")
    assert kv.get("prs") is None and KvRepository(db, "git").keys() == []  # namespaced


def test_a_plugins_context_has_its_name_logger_and_own_directory(tmp_path: Path) -> None:
    config = Config.model_validate(
        {
            "email": "me@x",
            "db_path": (tmp_path / "d.db").as_posix(),
            "github": {"org": "acme"},
        }
    )
    db = Database(":memory:")
    migrate(config, db)
    github = next(e for e in enabled(config, db, NOW) if e.name == "github")
    assert github.ctx.name == "github"
    assert github.ctx.log.name == "move_forward.plugins.github"
    directory = github.ctx.data_dir
    assert directory == tmp_path / "plugins" / "github" and directory.is_dir()
    if sys.platform != "win32":  # Windows' access lists are private.py's own test
        assert private.is_private(directory)
    assert github.ctx.data_dir == directory  # made once


def test_a_context_without_a_place_for_files_says_so() -> None:
    config = Config.model_validate({"email": "me@x", "github": {"org": "acme"}})
    db = Database(":memory:")
    migrate(config, db)
    ctx = next(e for e in enabled(config, db, NOW) if e.name == "github").ctx
    bare = type(ctx)(
        config=ctx.config,
        email=ctx.email,
        teams=[],
        prune_days=90,
        now=NOW,
        db=db,
        kv=ctx.kv,
    )
    with pytest.raises(RuntimeError, match="no data directory"):
        _ = bare.data_dir
