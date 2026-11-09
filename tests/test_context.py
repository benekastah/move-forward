"""What a plugin's context gives it: its name, a logger, and kv."""

from conftest import NOW

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


def test_a_plugins_context_has_its_name_and_logger() -> None:
    config = Config.model_validate({"email": "me@x", "github": {"org": "acme"}})
    db = Database(":memory:")
    migrate(config, db)
    github = next(e for e in enabled(config, db, NOW) if e.name == "github")
    assert github.ctx.name == "github"
    assert github.ctx.log.name == "move_forward.plugins.github"
