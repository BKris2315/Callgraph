"""
Getting the source tree to analyse: a commit exported with ``git archive``, or the working tree.

A commit is exported into a temporary directory together with each submodule at the SHA that
commit records, so the graph always describes one consistent snapshot. Nothing is checked out
in the user's repository and nothing from it is imported. A folder that is not a git repository
is read as it is.
"""

import contextlib
import datetime
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
from typing import Dict, Iterator


@dataclass
class Snapshot:
    """Where the source lives on disk and which commit it is."""

    root: str
    sha: str
    subject: str
    date: str  # committer date, YYYY-MM-DD
    submodules: Dict[str, str] = field(default_factory=dict)  # path -> sha
    worktree: bool = False
    repo: str = ""  # the repository (or plain folder) the snapshot was taken from

    @property
    def label(self) -> str:
        """Short description for messages, e.g. ``commit 1a2b3c4d5e``."""
        if not self.sha.strip("0"):
            return f"folder {self.repo}"
        return f"commit {self.sha[:10]}" + (" + working tree" if self.worktree else "")

    def as_meta(self) -> dict:
        return {"sha": self.sha, "subject": self.subject, "date": self.date, "submodules": dict(self.submodules)}


class SourceError(RuntimeError):
    """The requested snapshot cannot be produced (unknown commit, missing submodule commit, ...)."""


def git(repo: str, *args: str, binary: bool = False):
    """Run ``git -C repo args...`` and return stdout (text, stripped, unless ``binary``)."""
    try:
        out = subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        raise SourceError(f"git {' '.join(args)} failed in {repo}: {e.stderr.decode().strip()}") from e
    return out.stdout if binary else out.stdout.decode().strip()


def _untar(blob: bytes, dest: str) -> None:
    with tarfile.open(fileobj=io.BytesIO(blob)) as t:
        try:
            t.extractall(dest, filter="data")
        except TypeError:  # Python < 3.12
            t.extractall(dest)


def export_commit(repo: str, commit: str, dest: str) -> Snapshot:
    """Export ``commit`` of ``repo`` (and its submodules at the recorded SHAs) into ``dest``."""
    sha = git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}")
    _untar(git(repo, "archive", "--format=tar", sha, binary=True), dest)
    subs = {}
    for line in git(repo, "ls-tree", "-r", sha).splitlines():
        meta, path = line.split("\t", 1)
        mode, _kind, sub_sha = meta.split()
        if mode != "160000":  # not a submodule (gitlink)
            continue
        sub_repo = os.path.join(repo, path)
        try:
            git(sub_repo, "cat-file", "-e", f"{sub_sha}^{{commit}}")
        except (SourceError, FileNotFoundError):
            raise SourceError(
                f"Submodule {path} is recorded at {sub_sha[:10]}, which is not in {sub_repo}.\n"
                f"Fetch it first:  git -C {sub_repo} fetch origin"
            )
        target = os.path.join(dest, path)
        os.makedirs(target, exist_ok=True)
        _untar(git(sub_repo, "archive", "--format=tar", sub_sha, binary=True), target)
        subs[path] = sub_sha
    return Snapshot(dest, sha, git(repo, "log", "-1", "--format=%s", sha), git(repo, "log", "-1", "--format=%cs", sha), subs, repo=repo)


def is_git_repo(path: str) -> bool:
    try:
        return git(path, "rev-parse", "--is-inside-work-tree") == "true"
    except (SourceError, FileNotFoundError):
        return False


def folder_snapshot(path: str) -> Snapshot:
    """A plain folder (no git): read as it is."""
    today = datetime.date.today().isoformat()
    return Snapshot(path, "0" * 40, "(not a git repository)", today, {}, worktree=True, repo=path)


def worktree_snapshot(repo: str) -> Snapshot:
    """The working tree as it is, uncommitted edits included (labelled with HEAD)."""
    head = git(repo, "rev-parse", "HEAD")
    dirty = bool(git(repo, "status", "--porcelain", "--ignore-submodules=none"))
    subject = git(repo, "log", "-1", "--format=%s") + (" (plus uncommitted changes)" if dirty else "")
    return Snapshot(repo, head, subject, git(repo, "log", "-1", "--format=%cs"), {}, worktree=True, repo=repo)


@contextlib.contextmanager
def snapshot(repo: str, rev: str = "HEAD", worktree: bool = False) -> Iterator[Snapshot]:
    """Context manager yielding a :class:`Snapshot`; a temporary export is removed on exit.

    Inside a git repository the snapshot covers the whole repository (its top level), whichever
    sub-folder ``repo`` points at. Anything else is read as a plain folder.
    """
    repo = os.path.abspath(os.path.expanduser(repo))
    if not os.path.isdir(repo):
        raise SourceError(f"repository not found: {repo}")
    if not is_git_repo(repo):
        yield folder_snapshot(repo)
        return
    repo = git(repo, "rev-parse", "--show-toplevel")
    try:
        git(repo, "rev-parse", "--verify", "HEAD")
    except SourceError:  # a repository without commits yet
        yield folder_snapshot(repo)
        return
    if worktree:
        yield worktree_snapshot(repo)
        return
    tmp = tempfile.mkdtemp(prefix="callgraph-")
    try:
        yield export_commit(repo, rev, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
