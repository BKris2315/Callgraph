"""
The rendered-layout check. The report formatting is tested everywhere; the browser tests run only
where Playwright and a working Chromium are available, and are skipped otherwise.
"""

import os

import pytest

from callgraph import Overlay, build_page, render_html
from callgraph.layout_check import CheckUnavailable, Issue, ViewResult, check_page, format_report
from callgraph.render import WEB_DIR
from callgraph.source import Snapshot
from conftest import MINIREPO


def test_report_lists_errors_and_hides_warnings_by_default():
    res = [
        ViewResult("main", "AUTO", "DOWN", 9, [
            Issue("node-overlap", "error", "a", "b", "f", "g", 12.0, 4.0),
            Issue("edge-through-node", "warning", "e1", "c", "f → h #1", "c", 0, 0),
        ]),
        ViewResult("parts", "AUTO", "RIGHT", 3, []),
    ]
    text = format_report(res)
    rows = [line.split() for line in text.splitlines()[1:3]]
    assert rows == [["main", "auto→down", "9", "1", "1"], ["parts", "auto→right", "3", "0", "0"]]
    assert "node-overlap" in text and "f  ↔  g  (12×4px)" in text
    assert "edge-through-node" not in text
    assert "edge-through-node" in format_report(res, show_warnings=True)
    assert text.rstrip().endswith("1 errors, 1 warnings in 2 view layouts")
    assert res[0].to_dict()["errors"] == 1


# ---------------------------------------------------------------------------- browser tests
@pytest.fixture(scope="module")
def page_file(tmp_path_factory, ix, rs, spec):
    data = build_page(ix, spec, Overlay(), Snapshot(MINIREPO, "a" * 40, "test", "2026-01-01"), rs).data
    path = tmp_path_factory.mktemp("page") / "page.html"
    path.write_text(render_html(data, spec.TITLE, spec.SEARCH_EXAMPLE), encoding="utf-8")
    return str(path)


@pytest.fixture()
def browser():  # function scope: check_page starts its own Playwright, which cannot nest inside this one
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # no browser binary or missing system libraries
            pytest.skip(f"Chromium unavailable: {str(e).splitlines()[0]}")
        yield b
        b.close()


def test_generated_page_has_no_overlaps(page_file, tmp_path):
    try:
        results = check_page(page_file, directions=("AUTO", "DOWN", "RIGHT"), screenshots_dir=str(tmp_path))
    except CheckUnavailable as e:  # no Playwright, or no working Chromium
        pytest.skip(str(e).splitlines()[0])
    assert {r.view for r in results} == {"main", "parts"}
    assert all(r.nodes > 0 for r in results)
    errors = [(r.view, r.direction, i.kind, i.a_name, i.b_name) for r in results for i in r.errors]
    assert errors == []


def test_check_detects_a_planted_overlap(page_file, browser):
    """Guard against a vacuous check: stack two functions and their texts must be reported."""
    page = browser.new_page(viewport={"width": 1200, "height": 800})
    page.goto("file://" + page_file + "#main")
    page.wait_for_function("window.CG && document.getElementById('cy').dataset.layout === 'done'")
    with open(os.path.join(WEB_DIR, "check_overlaps.js"), encoding="utf-8") as fh:
        page.add_script_tag(content=fh.read())
    kinds = page.evaluate("""() => {
      const cy = CG.cy();
      const fns = cy.nodes('.fn');
      fns[1].position(Object.assign({}, fns[0].position()));
      return CGCheck.run(cy, { tolerance: 1 }).map((i) => i.kind);
    }""")
    page.close()
    assert "node-overlap" in kinds
