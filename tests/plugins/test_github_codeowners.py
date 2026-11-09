from pathlib import Path

from move_forward.plugins.github import codeowners

FILE = """\
# comment
*                       @org/everyone
/.ci/            @org/ci
/.ci/steps/demo-*.sh @org/demo
/app/api/               @org/api #required
/app/api/Public.java    @org/api @org/public
*.md                    @org/docs
docs/                   @org/docs-dir
/client/pages/\\[id\\]/page.tsx @org/web
"""


def owners(path: str) -> tuple[tuple[str, ...], bool]:
    return codeowners.CodeOwners(FILE, required_marker="#required").owners(path)


def test_last_matching_line_wins() -> None:
    assert owners(".ci/pipeline.yml") == (("@org/ci",), False)
    assert owners(".ci/steps/demo-env.sh") == (("@org/demo",), False)
    assert owners("app/api/Public.java") == (("@org/api", "@org/public"), False)


def test_directory_rule_covers_descendants_and_marks_required() -> None:
    assert owners("app/api/deep/Thing.java") == (("@org/api",), True)


def test_unanchored_patterns_match_anywhere() -> None:
    assert owners("README.md") == (("@org/docs",), False)
    assert owners("a/b/notes.md") == (("@org/docs",), False)
    assert owners("x/docs/guide.txt") == (("@org/docs-dir",), False)


def test_catch_all_and_escaped_brackets() -> None:
    assert owners("random/file.py") == (("@org/everyone",), False)
    assert owners("client/pages/[id]/page.tsx") == (("@org/web",), False)


def test_a_wildcard_last_names_only_what_it_matches_and_a_leading_globstar_matches_the_root() -> (
    None
):
    rules = codeowners.CodeOwners(
        "docs/* @docs\n**/logs @logs\napps/**/build/ @build\n/packages/*-ui @ui\nfoo**/bar @x\n"
    )
    assert rules.owners("docs/getting-started.md") == (("@docs",), False)
    assert rules.owners("docs/build-app/troubleshooting.md") == ((), False)  # GitHub's example
    assert rules.owners("logs/x.log") == (("@logs",), False)
    assert rules.owners("deploy/logs/x.log") == (("@logs",), False)
    assert rules.owners("apps/build/out.js") == (("@build",), False)
    assert rules.owners("apps/web/build/out.js") == (("@build",), False)
    assert rules.owners("packages/web-ui/src/a.ts") == (("@ui",), False)  # not a bare `*`
    assert rules.owners("foobar") == ((), False)  # `**/` only as a whole segment
    assert rules.owners("foo-x/bar") == (("@x",), False)


def test_required_needs_the_configured_marker() -> None:
    assert codeowners.CodeOwners(FILE).owners("app/api/x") == (("@org/api",), False)
    assert codeowners.CodeOwners(FILE, "#!must").owners("app/api/x") == (("@org/api",), False)


def test_unowned_when_no_rule_matches() -> None:
    assert codeowners.CodeOwners("/a/ @x\n").owners("b/c") == ((), False)


def test_load_prefers_github_dir(tmp_path: Path) -> None:
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "CODEOWNERS").write_text("/a/ @gh\n")
    (tmp_path / "CODEOWNERS").write_text("/a/ @root\n")
    loaded = codeowners.load(tmp_path)
    assert loaded is not None
    assert loaded.owners("a/x") == (("@gh",), False)
    assert codeowners.load(tmp_path / "missing") is None
