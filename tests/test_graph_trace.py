import re

from callgraph import CallGraph, format_tree, trace


def test_callees_in_source_order(graph):
    order = [e.callee for e in graph.callees("main.py::main")]
    assert order == [
        "app/util/io.py::load_config",
        "app/pipeline.py::Pipeline.__init__",
        "app/pipeline.py::Pipeline.describe",
        "app/pipeline.py::Pipeline.run",
    ]


def test_edges_carry_condition_and_loop(graph):
    by_callee = {e.callee: e for e in graph.callees("main.py::main")}
    assert by_callee["app/pipeline.py::Pipeline.describe"].condition == "debug"
    assert by_callee["app/pipeline.py::Pipeline.run"].loop == "for batch in cfg['batches']"
    assert not by_callee["app/util/io.py::load_config"].conditional


def test_callers(graph):
    callers = [e.caller for e in graph.callers("app/model/workers.py::Scorer.__call__")]
    assert set(callers) == {"app/pipeline.py::Pipeline.run", "app/model/workers.py::SimpleWorker.step"}


def test_skip_patterns(ix, rs):
    g = CallGraph(ix, rs, [re.compile(r"::log$")])
    assert "app/util/io.py::log" not in [e.callee for e in g.callees("app/model/workers.py::SimpleWorker.prepare")]


def test_trace_down_depth_and_cut(graph):
    root = trace(graph, "main.py::main", depth=1)
    assert [c.order for c in root.children] == [1, 2, 3, 4]
    run = next(c for c in root.children if c.fid == "app/pipeline.py::Pipeline.run")
    assert run.children == [] and run.cut  # has callees, depth reached


def test_trace_marks_repeats_instead_of_expanding(graph):
    root = trace(graph, "main.py::main", depth=5)
    # score_unique_name is reached via Pipeline.describe (expanded) and again via Pipeline.run -> Scorer.__call__
    hits = [n for n in _walk(root) if n.fid == "app/model/workers.py::Scorer.score_unique_name"]
    assert len(hits) == 2 and [n.seen for n in hits] == [False, True]


def test_trace_up(graph):
    root = trace(graph, "app/model/workers.py::Scorer.score_unique_name", depth=3, up=True)
    names = {n.fid for n in _walk(root)}
    assert "main.py::main" in names


def test_trace_include_stops_at_boundary(graph):
    root = trace(graph, "main.py::main", depth=5, include=[r"^main\.py$"])
    assert all(c.outside and not c.children for c in root.children)


def test_format_tree(graph):
    text = format_tree(graph, trace(graph, "main.py::main", depth=1), show_args=True)
    lines = text.splitlines()
    assert lines[0].startswith("main  main.py:")
    assert lines[3].startswith("├─ 3 Pipeline.describe") and "[if debug]" in lines[3]
    assert lines[-1].startswith("└─ 4 Pipeline.run") and "(batch)" in lines[-1]


def test_trace_to_dict(graph):
    d = trace(graph, "main.py::main", depth=1).to_dict(graph)
    assert d["label"] == "main" and d["children"][0]["order"] == 1


def _walk(n):
    yield n
    for c in n.children:
        yield from _walk(c)
