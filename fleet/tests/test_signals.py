from git_helpers import commit, git, init_repo

from fleet.history import signals


def test_changelog_fragment_is_the_projects_own_label():
    assert signals.changelog_signal(["src/a.py", "changelog/123.bugfix.rst"])["kind"] == signals.BUG_FIX
    assert signals.changelog_signal(["newsfragments/9.feature.md"])["kind"] == signals.FEATURE
    # A substantive fragment outranks a doc note shipped beside it.
    assert signals.changelog_signal(["changelog/1.doc.rst", "changelog/1.bugfix.rst"])["kind"] == signals.BUG_FIX
    assert signals.changelog_signal(["CHANGELOG.md"]) == {"kind": None, "edited_changelog": ["CHANGELOG.md"]}
    assert signals.changelog_signal(["src/a.py"]) is None


def test_message_signal_is_weak_and_says_so():
    assert signals.message_signal("fix(core): handle None")["kind"] == signals.BUG_FIX
    assert signals.message_signal("feat: add --scalar")["evidence"] == "prefix feat:"
    assert signals.message_signal("Simplify the loader")["kind"] == signals.REFACTOR
    assert signals.message_signal("Update core.py")["kind"] == signals.UNKNOWN


def test_size_tiers():
    assert [signals.size_tier(n) for n in (1, 2, 6, 21)] == ["single_file", "multi_file", "wide", "sweeping"]


def test_reverts_and_backports_come_from_git_trailers(tmp_path):
    repo = init_repo(tmp_path / "repo")
    commit(repo, "src/a.py", "A = 1\n", "base", date="2024-01-01T00:00:00Z")
    bad = commit(repo, "src/a.py", "A = 2\n", "change", date="2024-01-02T00:00:00Z")
    (repo / "src/a.py").write_text("A = 1\n")
    git(repo, "add", "src/a.py")
    git(repo, "commit", "-m", f'Revert "change"\n\nThis reverts commit {bad}.', date="2024-01-03T00:00:00Z")
    revert = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "--quiet", "-b", "release")
    (repo / "src/b.py").write_text("B = 1\n")
    git(repo, "add", "src/b.py")
    git(repo, "commit", "-m", f"backport\n\n(cherry picked from commit {revert})", date="2024-01-04T00:00:00Z")
    reverted, backported = signals.history_links(repo)
    assert reverted[bad] == revert
    assert backported[revert] == [git(repo, "rev-parse", "HEAD")]


def test_the_same_edit_across_many_files_is_mechanical(tmp_path):
    repo = init_repo(tmp_path / "repo")
    paths = [f"src/m{n}.py" for n in range(4)]
    for path in paths:
        commit(repo, path, "from old import thing\nX = 1\n", "base", date="2024-01-01T00:00:00Z")
    base = git(repo, "rev-parse", "HEAD")
    for path in paths:
        (repo / path).write_text("from new import thing\nX = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "move", date="2024-01-02T00:00:00Z")
    shape = signals.diff_signal(repo, base, git(repo, "rev-parse", "HEAD"), paths)
    assert shape["mechanical"] and shape["repeated_edit_share"] == 1.0
