import os
import shutil

from callgraph import CallGraph, Index, Overlay, Resolver, build_page, load_spec
from callgraph.autospec import detect_include, group_key
from callgraph.source import folder_snapshot
from conftest import MINIREPO


def test_detect_include_skips_tests_and_hidden(tmp_path):
    for d in ("pkg", "tests", ".venv/lib", "docs"):
        os.makedirs(tmp_path / d)
        (tmp_path / d / "x.py").write_text("def f():\n    pass\n")
    (tmp_path / "setup.py").write_text("")
    (tmp_path / "tool.py").write_text("")
    assert detect_include(str(tmp_path)) == ["pkg", "tool.py"]


def test_detect_include_reads_notebooks_and_scripts_next_to_src(tmp_path):
    os.makedirs(tmp_path / "src" / "pkg")
    (tmp_path / "src" / "pkg" / "a.py").write_text("")
    os.makedirs(tmp_path / "scripts")
    (tmp_path / "scripts" / "b.py").write_text("")
    os.makedirs(tmp_path / "notebooks" / ".ipynb_checkpoints")
    (tmp_path / "notebooks" / "n.ipynb").write_text("{}")
    assert detect_include(str(tmp_path)) == ["notebooks", "scripts", "src"]


def test_group_key_levels():
    assert group_key("src/pkg/sub/mod.py", 1) == ("pkg", "src/pkg/")
    assert group_key("src/pkg/sub/mod.py", 2) == ("pkg/sub", "src/pkg/sub/")
    assert group_key("src/pkg/mod.py", 2) == ("pkg/mod.py", "src/pkg/mod.py")
    assert group_key("main.py", 1) == ("main.py", "main.py")


def _auto_page():
    spec = load_spec("auto")
    spec.INDEX_INCLUDE = detect_include(MINIREPO)
    ix = Index(MINIREPO, spec.INDEX_INCLUDE, spec.INDEX_EXCLUDE)
    return build_page(ix, spec, Overlay(), folder_snapshot(MINIREPO), Resolver(ix), repo_name="minirepo")


def test_auto_views_start_from_entry_points():
    page = _auto_page()
    spec = page.spec
    assert spec.TITLE == "minirepo"
    roots = {r for v in spec.VIEWS for r in v["roots"]}
    assert spec.VIEWS[0]["roots"] == ["main.py::main"]  # the top-level script drives the code: first view
    assert "app/pipeline.py::Pipeline.run" in roots  # called only by that script: a library entry point
    assert "app/model/base.py::Base.prepare" not in roots  # called inside the library
    assert all(v["roots"] for v in spec.VIEWS)


def test_auto_overview_links_views():
    data = _auto_page().data
    assert data["start"] == "overview"
    ov = data["views"]["overview"]
    assert {n["goto"] for n in ov["nodes"]} == set(data["views"]) - {"overview"}
    assert ov["edges"], "main.py calls into app/, so the overview has an arrow"
