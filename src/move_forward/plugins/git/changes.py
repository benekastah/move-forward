"""Telling cheaply that a repository moved, for `GitPlugin.changed`: a commit, a checkout, a
branch made or deleted, a rebase, a push or a fetch. They all rewrite files git keeps its
refs in, so the times, sizes and identities of those files say so, without running git.

Edits not yet committed aren't seen: the plugin's `refresh_seconds` catches those. The index
is left out, because editors rewrite it whenever they ask git for the status.
"""

import hashlib
from pathlib import Path


def common_dir(repo: Path) -> Path | None:
    """The directory holding a checkout's refs: its `.git`, or for a linked worktree (whose
    `.git` is a file naming its own git directory) the main repository's. None when `repo`
    isn't a checkout."""
    dot = repo / ".git"
    if dot.is_dir():
        return dot
    if not dot.is_file():
        return None
    text = dot.read_text().strip()
    if not text.startswith("gitdir:"):
        return None
    own = (repo / text.removeprefix("gitdir:").strip()).resolve()
    commondir = own / "commondir"
    return (own / commondir.read_text().strip()).resolve() if commondir.is_file() else own


def _ref_files(common: Path) -> list[Path]:
    """The files whose changes mean the repository moved: HEAD, each worktree's HEAD, the
    packed refs and every loose ref (branches, remote branches, tags, the stash)."""
    return [
        common / "HEAD",
        common / "packed-refs",
        *(common / "worktrees").glob("*/HEAD"),
        *(path for path in (common / "refs").rglob("*") if path.is_file()),
    ]


def fingerprint(repos: list[Path]) -> str:
    """A digest of the ref files of `repos` (each repository once, however many of its
    worktrees are listed) that changes when any of them does."""
    digest = hashlib.sha256()
    commons = {common for repo in repos if (common := common_dir(repo))}
    for common in sorted(commons):
        for path in sorted(_ref_files(common)):
            try:
                stat = path.stat()
            except OSError:  # gone since it was listed, or never there (packed-refs)
                continue
            digest.update(f"{path}\0{stat.st_mtime_ns}\0{stat.st_size}\0{stat.st_ino}\n".encode())
    return digest.hexdigest()
