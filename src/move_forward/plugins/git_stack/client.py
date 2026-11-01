"""Reads git-stack's stacks with `git stack info`."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ...shell import git
from .models import GitStackSnapshot, StackBranch, StackRepo


def parse_info(out: str) -> list[StackBranch]:
    """The branches in `git stack info --json` output."""
    return [StackBranch.model_validate(entry) for entry in json.loads(out)]


def collect_repo(repo: Path) -> StackRepo:
    out = git(repo, "stack", "info", "--json", "--offline")
    return StackRepo(dir=str(repo), branches=parse_info(out))


def collect(repos: list[Path]) -> GitStackSnapshot:
    with ThreadPoolExecutor(max_workers=max(1, len(repos))) as pool:
        return GitStackSnapshot(repos=list(pool.map(collect_repo, repos)))
