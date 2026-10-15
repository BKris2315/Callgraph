"""
`callgraph annotate` with a fake model: which functions get described, caching, hand-written
entries, prompts and how the page labels model text. A real model runs only when
CALLGRAPH_TEST_MODEL names one (e.g. a small local folder or Hub id).
"""

import os

import pytest

from callgraph import CallGraph, Overlay, build_page
from callgraph.annotate import PROMPT_VERSION, Backend, annotate, clean, function_prompt
from callgraph.source import Snapshot
from conftest import MINIREPO

SNAP = Snapshot(MINIREPO, "a" * 40, "test", "2026-01-01")


class FakeModel(Backend):
    """Deterministic stand-in: answers with the function or view named in the prompt."""

    name = "fake/model"

    def __init__(self):
        self.calls = 0

    def generate(self, prompts):
        self.calls += len(prompts)
        out = []
        for messages in prompts:
            first = messages[-1]["content"].splitlines()[1]  # "Function: X" or "Entry points: ..."
            out.append(f"**Describes** {first.split(': ', 1)[1]}. It does things.")
        return out


@pytest.fixture()
def page(ix, rs, spec):
    return build_page(ix, spec, Overlay(), SNAP, rs).data


def test_describes_only_undocumented_functions_by_default(ix, graph, page):
    overlay, st = annotate(ix, graph, page, FakeModel(), commit="c" * 40)
    described = set(overlay["nodes"])
    assert "main.py::main" not in described  # has a docstring
    assert "app/pipeline.py::Pipeline.run" in described  # has none
    entry = overlay["nodes"]["app/pipeline.py::Pipeline.run"]
    assert entry["desc"] == "Describes Pipeline.run. It does things."  # markdown cleaned
    assert entry["model"] == "fake/model" and entry["body_hash"] == ix.funcs["app/pipeline.py::Pipeline.run"].body_hash
    assert entry["prompt_version"] == PROMPT_VERSION
    assert st.generated == len(described) and st.skipped_documented > 0
    assert set(overlay["views"]) == {"main", "parts"} and st.views == 2


def test_all_flag_includes_documented(ix, graph, page):
    overlay, _ = annotate(ix, graph, page, FakeModel(), only_missing=False, views=False)
    assert "main.py::main" in overlay["nodes"] and overlay["views"] == {}


def test_second_run_is_served_from_cache(ix, graph, page):
    first, _ = annotate(ix, graph, page, FakeModel())
    model = FakeModel()
    second, st = annotate(ix, graph, page, model, existing=first)
    assert model.calls == 0 and st.generated == 0 and st.kept == len(first["nodes"])
    assert second["nodes"] == first["nodes"] and second["views"] == first["views"]


def test_changed_function_is_regenerated(ix, graph, page):
    first, _ = annotate(ix, graph, page, FakeModel(), views=False)
    first["nodes"]["app/pipeline.py::Pipeline.run"]["body_hash"] = "old"
    model = FakeModel()
    _, st = annotate(ix, graph, page, model, existing=first, views=False)
    assert st.generated == 1 and st.targets == ["app/pipeline.py::Pipeline.run"]


def test_older_prompt_version_is_regenerated(ix, graph, page):
    first, _ = annotate(ix, graph, page, FakeModel())
    for e in list(first["nodes"].values()) + list(first["views"].values()):
        e["prompt_version"] = PROMPT_VERSION - 1
    _, st = annotate(ix, graph, page, FakeModel(), existing=first)
    assert st.kept == 0 and st.generated == len(first["nodes"]) and st.views == len(first["views"])


def test_hand_written_entries_are_never_replaced(ix, graph, page):
    mine = {"nodes": {"app/pipeline.py::Pipeline.run": {"desc": "Mine."}}, "views": {"main": {"intro": ["My intro."]}}}
    overlay, st = annotate(ix, graph, page, FakeModel(), existing=mine, refresh=True)
    assert overlay["nodes"]["app/pipeline.py::Pipeline.run"] == {"desc": "Mine."}
    assert overlay["views"]["main"] == {"intro": ["My intro."]} and st.human == 1


def test_prompt_has_source_and_neighbours(ix, graph):
    text = function_prompt(ix, graph, "app/pipeline.py::Pipeline.run")[-1]["content"]
    assert "Function: Pipeline.run" in text and "def run(self, batch):" in text
    assert "Base.run" in text and "Called by: main" in text


def test_clean():
    assert clean("<think>hmm</think> Answer: `x` does **y**.") == "X does y."
    assert clean("This function calls get_connection_with_tls_context and *then* send_.") == \
        "Calls get_connection_with_tls_context and then send_."
    long = "First sentence. " * 60
    assert clean(long).endswith(".") and len(clean(long)) <= 420


def test_page_labels_model_text(ix, rs, spec, graph, page):
    overlay, _ = annotate(ix, graph, page, FakeModel())
    data = build_page(ix, spec, Overlay(overlay), SNAP, rs).data
    node = next(n for n in data["views"]["main"]["nodes"] if n["id"] == "app/pipeline.py::Pipeline.run")
    assert node["desc"].startswith("Describes") and "fake/model" in node["note"]
    v = data["views"]["main"]
    assert v["intro_by"] == "fake/model" and v["intro"][-1].startswith("Starts from")


@pytest.mark.skipif(not os.environ.get("CALLGRAPH_TEST_MODEL"), reason="set CALLGRAPH_TEST_MODEL to run a real model")
def test_real_model_is_deterministic(ix, graph, page):
    from callgraph.annotate import TransformersBackend

    m = TransformersBackend(os.environ["CALLGRAPH_TEST_MODEL"], max_new_tokens=60)
    a, _ = annotate(ix, graph, page, m, views=False, limit=3)
    b, _ = annotate(ix, graph, page, m, views=False, limit=3, refresh=True)
    assert a["nodes"] == b["nodes"] and all(e["desc"] for e in a["nodes"].values())
