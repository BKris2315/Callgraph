"""Shared fixtures: the mini repo as an index, and as a throwaway git repository."""

import os
import shutil
import subprocess

import pytest

from callgraph import CallGraph, Index, Resolver, load_spec

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
MINIREPO = os.path.join(FIXTURES, "minirepo")
MINISPEC = os.path.join(FIXTURES, "minispec.py")


@pytest.fixture(scope="session")
def spec():
    return load_spec(MINISPEC)


@pytest.fixture(scope="session")
def ix(spec):
    return Index(MINIREPO, spec.INDEX_INCLUDE, spec.INDEX_EXCLUDE)


@pytest.fixture(scope="session")
def rs(ix):
    return Resolver(ix)


@pytest.fixture(scope="session")
def graph(ix, rs):
    return CallGraph(ix, rs)


def _git(repo, *args):
    # Isolated identity, no commit signing: the user's global git config must not matter here.
    cfg = ["-c", "user.name=test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "-C", repo, *cfg, *args], check=True, capture_output=True)


@pytest.fixture()
def git_repo(tmp_path):
    """The mini repo committed into a fresh git repository; returns its path."""
    repo = str(tmp_path / "repo")
    shutil.copytree(MINIREPO, repo)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "initial")
    return repo
