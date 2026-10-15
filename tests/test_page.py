import json
import re

import pytest

from callgraph import Overlay, build_page, render_html
from callgraph.source import Snapshot
from conftest import MINIREPO

SNAP = Snapshot(MINIREPO, "a" * 40, "test commit", "2026-01-01")


@pytest.fixture(scope="module")
def page(ix, rs, spec):
    return build_page(ix, spec, Overlay(), SNAP, rs)


def test_views_and_sections(page):
    assert list(page.data["views"]) == ["main", "parts"]
    assert page.data["start"] == "main"
    assert page.data["sections"] == [{"title": "Views", "views": ["main", "parts"]}]


def test_missing_root_is_a_warning(page):
    assert any("main.py::does_not_exist" in w for w in page.warnings)


def test_nodes_grouped_by_file_and_skips_noise(page):
    v = page.data["views"]["main"]
    ids = {n["id"] for n in v["nodes"]}
    assert "app/util/io.py::log" not in ids  # SKIP
    assert {g["id"] for g in v["groups"]} == {"g:" + n["loc"].split(":")[0] for n in v["nodes"]}
    node = next(n for n in v["nodes"] if n["id"] == "main.py::main")
    assert node["loc"] == "main.py:7" and node["stage"] == "slate" and node["desc"].startswith("Load the config")


def test_edges_are_numbered_per_caller_with_conditions(page):
    v = page.data["views"]["main"]
    out = sorted((e for e in v["edges"] if e[0] == "main.py::main"), key=lambda e: e[2])
    assert [e[2] for e in out] == [1, 2, 3, 4]
    describe = next(e for e in out if e[1].endswith("Pipeline.describe"))
    assert describe[4] == "debug"
    run = next(e for e in out if e[1].endswith("Pipeline.run"))
    assert run[3] == "per batch in cfg['batches'] · run(batch)"


def test_pipeline_groups_are_file_groups(page):
    assert all(g["kind"] == "file" for v in page.data["views"].values() for g in v["groups"])


def test_boundary_nodes_outside_include(page):
    v = page.data["views"]["parts"]
    ext = {n["id"] for n in v["nodes"] if n.get("ext")}
    assert ext == set()  # Base.run only calls into base.py
    assert all(n["group"] == "g:app/model/base.py" for n in v["nodes"])


def test_goto_links_between_views(page):
    v = page.data["views"]["main"]
    base_run = next(n for n in v["nodes"] if n["id"] == "app/model/base.py::Base.run")
    assert base_run["goto"] == "parts"


def test_overlay_text_and_stale_note(ix, rs, spec):
    overlay = Overlay({
        "views": {"main": {"intro": ["Custom intro."], "notes": ["A note."], "commit": "b" * 40}},
        "nodes": {"main.py::main": {"desc": "Custom.", "body_hash": "deadbeef", "commit": "c" * 40}},
    })
    data = build_page(ix, spec, overlay, SNAP, rs).data
    v = data["views"]["main"]
    assert v["intro"] == ["Custom intro."]
    assert v["notes"] == ["A note. (noted at bbbbbbbb)"]
    node = next(n for n in v["nodes"] if n["id"] == "main.py::main")
    assert node["desc"] == "Custom." and "cccccccc" in node["note"]


def test_render_is_self_contained(page, spec):
    html = render_html(page.data, spec.TITLE, spec.SEARCH_EXAMPLE)
    assert html.startswith("<!doctype html>")
    assert "<title>MiniRepo Call Graph</title>" in html and "e.g. run" in html
    assert "/*__" not in html and "{{" not in html  # every placeholder filled
    for module in ("window.CGLayout", "window.CGMinimap", "elk.bundled.js", 'id="minimap"'):
        assert module in html, module
    m = re.search(r"window\.CALLGRAPH = (.*?);\n</script>", html, re.S)
    assert json.loads(m.group(1).replace("<\\/", "</"))["views"].keys() == page.data["views"].keys()


def test_render_escapes_script_close(page):
    data = json.loads(json.dumps(page.data))
    data["views"]["main"]["nodes"][0]["desc"] = "</script><b>x"
    assert "</script><b>" not in render_html(data)
