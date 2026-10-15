"""
A research-style repository: a loose `src/` package, notebooks with IPython syntax that reach it
through `sys.path`, a script written as top-level code, and helper modules only notebooks use.
"""

import json
import os

import pytest

from callgraph import CallGraph, Index, Overlay, Resolver, build_page, load_spec, trace
from callgraph.autospec import detect_include
from callgraph.notebooks import notebook_source
from callgraph.source import folder_snapshot


def _nb(cells):
    return json.dumps({"cells": [{"cell_type": t, "metadata": {}, "source": src, **({"outputs": [], "execution_count": None} if t == "code" else {})}
                                 for t, src in cells], "metadata": {}, "nbformat": 4, "nbformat_minor": 5})


@pytest.fixture()
def research(tmp_path):
    lab = tmp_path / "src" / "lab"
    lab.mkdir(parents=True)
    (lab / "__init__.py").write_text("")
    (lab / "data.py").write_text("def load_csv(p):\n    return p\n\ndef normalize(x):\n    return x\n\n"
                                 "def load_dataset(p):\n    return normalize(load_csv(p))\n")
    (lab / "plots.py").write_text("def plot_signal(x):\n    print(x)\n")
    (lab / "unused.py").write_text("def old_helper():\n    return 1\n")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "train.py").write_text(
        "from lab.data import load_dataset\nfrom lab.plots import plot_signal\n\nx = load_dataset('a')\n"
        "if __name__ == '__main__':\n    plot_signal(x)\n")
    (tmp_path / "notebooks").mkdir()
    (tmp_path / "notebooks" / "01_explore.ipynb").write_text(_nb([
        ("markdown", "# Exploring the data\n\nFirst look."),
        ("code", "%matplotlib inline\nimport sys\nsys.path.append('../src/lab')\nimport data\n"),
        ("code", "!pip install foo\nfiles = !ls\nx = data.load_dataset('b.csv')\n"),
        ("code", "def broken(:\n"),  # a half-finished cell
        ("code", "%%time\nthis is not python\n"),
        ("code", "from lab.plots import plot_signal\nplot_signal(x)\ndata?\n"),
    ]))
    return str(tmp_path)


def _index(root):
    return Index(root, detect_include(root), load_spec("auto").INDEX_EXCLUDE)


def test_notebook_source_keeps_cells_and_line_counts():
    text = _nb([("markdown", "# Title\n\nmore"), ("code", "%time f()\nx = 1"), ("code", "%%bash\nls"), ("code", "y = (")])
    src, cells, skipped, doc = notebook_source(text)
    assert src.splitlines()[:2] == ["pass", "x = 1"] and doc == "Title"
    assert cells == [(1, 2), (3, 3), (5, 4)] and skipped == 1


def test_notebooks_and_scripts_are_indexed_as_drivers(research):
    ix = _index(research)
    nb, script = ix.by_path["notebooks/01_explore.ipynb"], ix.by_path["scripts/train.py"]
    assert nb.notebook and nb.driver and script.driver and not ix.by_path["src/lab/data.py"].driver
    assert nb.doc == "Exploring the data"
    assert any("1 cell(s) skipped" in e for e in ix.parse_errors)
    assert "notebooks/01_explore.ipynb::<module>" in ix.funcs and "scripts/train.py::<module>" in ix.funcs


def test_top_level_code_and_sys_path_imports_resolve(research):
    ix = _index(research)
    g = CallGraph(ix, Resolver(ix))
    nb = {e.callee for e in g.callees("notebooks/01_explore.ipynb::<module>")}
    assert nb == {"src/lab/data.py::load_dataset", "src/lab/plots.py::plot_signal"}  # `import data` via sys.path
    script = {e.callee: e for e in g.callees("scripts/train.py::<module>")}
    assert script["src/lab/plots.py::plot_signal"].condition == "__name__ == '__main__'"
    assert ix.loc("notebooks/01_explore.ipynb::<module>", 9) == "notebooks/01_explore.ipynb:cell 3"


def test_library_json_is_not_resolved_by_suffix(tmp_path):
    """The sys.path fallback is for notebooks and scripts only, and never for stdlib names."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "json.py").write_text("def dumps(x):\n    return x\n")
    (pkg / "util.py").write_text("import json\n\ndef f():\n    return json.dumps(1)\n")
    (tmp_path / "run.py").write_text("import json\njson.dumps(2)\n")
    ix = Index(str(tmp_path), ["pkg", "run.py"])
    g = CallGraph(ix)
    assert g.callees("pkg/util.py::f") == [] and g.callees("run.py::<module>") == []


def test_auto_page_for_a_research_repo(research):
    ix = _index(research)
    page = build_page(ix, load_spec("auto"), Overlay(), folder_snapshot(research), Resolver(ix), repo_name="research")
    views = page.spec.VIEWS
    assert [v["title"] for v in views[:2]] == ["notebooks", "scripts"]  # drivers first
    assert views[0]["roots"] == ["notebooks/01_explore.ipynb::<module>"]
    nodes = {n["id"]: n for n in page.data["views"][views[0]["id"]]["nodes"]}
    assert nodes["notebooks/01_explore.ipynb::<module>"]["label"] == "01_explore.ipynb"
    assert "src/lab/data.py::load_csv" in nodes  # walks into the library
    intro = " ".join(page.data["views"]["overview"]["intro"])
    assert "lab/unused.py" in intro and "lab/data.py" not in intro.split("reaches:")[-1]
    assert [r for v in views for r in v["roots"] if "unused" in r] == []


def test_find_notebook_by_file_name(research):
    ix = _index(research)
    assert ix.find("01_explore.ipynb") == ["notebooks/01_explore.ipynb::<module>"]
    assert ix.find("scripts/train.py") == ["scripts/train.py::<module>"]


def test_main_block_demos_do_not_hide_entry_points(tmp_path):
    """A library module whose `if __name__ == "__main__":` demo calls its own API keeps that API as entry point."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "table.py").write_text("def helper():\n    pass\n\ndef render():\n    helper()\n\n"
                                  "if __name__ == '__main__':\n    render()\n")
    ix = Index(str(tmp_path), ["pkg"])
    page = build_page(ix, load_spec("auto"), Overlay(), folder_snapshot(str(tmp_path)), Resolver(ix), repo_name="x")
    assert "pkg/table.py::render" in {r for v in page.spec.VIEWS for r in v["roots"]}


def test_imported_top_level_module_is_library_not_driver(tmp_path):
    """nanoGPT-style: model.py next to train.py, imported by it."""
    (tmp_path / "model.py").write_text("class GPT:\n    def forward(self):\n        pass\n")
    (tmp_path / "train.py").write_text("from model import GPT\nm = GPT()\nwhile True:\n    m.forward()\n")
    ix = Index(str(tmp_path), ["model.py", "train.py"])
    assert ix.by_path["train.py"].driver and not ix.by_path["model.py"].driver
    e = CallGraph(ix).callees("train.py::<module>")[-1]
    assert e.callee == "model.py::GPT.forward" and e.loop == "while True" and e.condition == ""


def test_annotate_prompt_for_notebook_code(research):
    from callgraph.annotate import function_prompt
    ix = _index(research)
    text = function_prompt(ix, CallGraph(ix), "notebooks/01_explore.ipynb::<module>")[-1]["content"]
    assert "top-level code of the notebook 01_explore.ipynb" in text
    assert "x = data.load_dataset('b.csv')" in text and "%matplotlib" not in text
    assert "load_dataset" in text.split("It calls:")[1]
