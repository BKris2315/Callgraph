import os

import pytest

from callgraph.source import SourceError, snapshot


def test_commit_export_is_temporary(git_repo):
    with snapshot(git_repo, "HEAD") as snap:
        root = snap.root
        assert os.path.isfile(os.path.join(root, "main.py"))
        assert len(snap.sha) == 40 and snap.subject == "initial"
        assert not snap.worktree
    assert not os.path.exists(root)


def test_worktree_reports_uncommitted_changes(git_repo):
    with open(os.path.join(git_repo, "main.py"), "a") as fh:
        fh.write("\n# edit\n")
    with snapshot(git_repo, worktree=True) as snap:
        assert snap.root == os.path.abspath(git_repo)
        assert snap.subject.endswith("(plus uncommitted changes)")


def test_unknown_revision(git_repo):
    with pytest.raises(SourceError):
        with snapshot(git_repo, "no-such-branch"):
            pass


def test_missing_repo(tmp_path):
    with pytest.raises(SourceError):
        with snapshot(str(tmp_path / "nope")):
            pass
