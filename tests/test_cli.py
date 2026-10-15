import json
import os

from callgraph.cli import main
from conftest import MINISPEC


def run(capsys, *argv):
    rc = main(list(argv))
    out, err = capsys.readouterr()
    return rc, out, err


def test_build_writes_html_and_json(git_repo, tmp_path, capsys):
    out = str(tmp_path / "out" / "page.html")
    rc, stdout, _ = run(capsys, "build", "--repo", git_repo, "--spec", MINISPEC, "-o", out, "--json")
    assert rc == 0 and "2 views" in stdout
    assert os.path.getsize(out) > 10_000
    assert json.load(open(out[:-5] + ".json"))["start"] == "main"


def test_trace(git_repo, capsys):
    rc, out, _ = run(capsys, "trace", "main", "-d", "1", "--repo", git_repo, "--spec", MINISPEC)
    assert rc == 0 and "├─ 1 load_config" in out


def test_trace_class_uses_init(git_repo, capsys):
    rc, out, _ = run(capsys, "trace", "Pipeline", "-d", "1", "--repo", git_repo, "--spec", MINISPEC)
    assert rc == 0 and out.startswith("Pipeline.__init__")


def test_trace_ambiguous_name_lists_ids(git_repo, capsys):
    rc, _, err = run(capsys, "trace", "run", "--repo", git_repo, "--spec", MINISPEC)
    assert rc == 2 and "app/model/base.py::Base.run" in err


def test_edges_from_json(git_repo, capsys):
    rc, out, _ = run(capsys, "edges", "--from", "main", "-f", "json", "--repo", git_repo, "--spec", MINISPEC)
    rows = json.loads(out)
    assert rc == 0 and [r["order"] for r in rows] == [1, 2, 3, 4]


def test_edges_of_view_tsv(git_repo, capsys):
    rc, out, _ = run(capsys, "edges", "--view", "main", "-f", "tsv", "--repo", git_repo, "--spec", MINISPEC)
    lines = out.strip().splitlines()
    assert rc == 0 and lines[0].startswith("caller\tcallee\torder") and len(lines) > 4


def test_unknown_view(git_repo, capsys):
    rc, _, err = run(capsys, "edges", "--view", "nope", "--repo", git_repo, "--spec", MINISPEC)
    assert rc == 2 and "available: main, parts" in err


def test_worktree_and_find(git_repo, capsys):
    rc, out, _ = run(capsys, "find", "step", "-w", "--repo", git_repo, "--spec", MINISPEC)
    assert rc == 0 and "SimpleWorker.step" in out and "Base.step" in out


def test_views(git_repo, capsys):
    rc, out, _ = run(capsys, "views", "--repo", git_repo, "--spec", MINISPEC)
    assert rc == 0 and "main" in out and "parts" in out


def test_bad_spec(capsys):
    rc, _, err = run(capsys, "views", "--spec", "nosuchspec")
    assert rc == 2 and "spec file not found" in err


def test_auto_build_needs_no_spec(git_repo, tmp_path, capsys):
    out = str(tmp_path / "auto.html")
    rc, stdout, _ = run(capsys, "build", "--repo", git_repo, "-o", out)
    assert rc == 0 and os.path.getsize(out) > 10_000
    assert "source: app, main.py" in stdout


def test_plain_folder_without_git(tmp_path, capsys):
    import shutil
    from conftest import MINIREPO
    folder = str(tmp_path / "plain")
    shutil.copytree(MINIREPO, folder)
    rc, out, _ = run(capsys, "trace", "main", "-d", "1", "--repo", folder)
    assert rc == 0 and out.startswith("main  main.py:")


def test_spec_command_prints_an_editable_spec(git_repo, tmp_path, capsys):
    rc, out, _ = run(capsys, "spec", "--repo", git_repo)
    assert rc == 0 and out.startswith('"""View spec for callgraph')
    path = tmp_path / "spec.py"
    path.write_text(out)
    rc, out2, _ = run(capsys, "views", "--repo", git_repo, "--spec", str(path))
    assert rc == 0 and "overview" in out2


def test_no_python_code(tmp_path, capsys):
    (tmp_path / "readme.txt").write_text("nothing here")
    rc, _, err = run(capsys, "build", "--repo", str(tmp_path))
    assert rc == 2 and "no Python code found" in err
