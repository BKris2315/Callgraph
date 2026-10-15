import pytest

from callgraph.resolver import INFERRED


def targets(ix, rs, fid, self_cls=None):
    f = ix.funcs[fid]
    return {t: note for c in f.calls for t, note in rs.resolve_call(c, f, self_cls)}


def test_imported_function_and_constructor(ix, rs):
    t = targets(ix, rs, "main.py::main")
    assert "app/util/io.py::load_config" in t
    assert "app/pipeline.py::Pipeline.__init__" in t  # Pipeline(cfg)


def test_local_assigned_from_constructor(ix, rs):
    t = targets(ix, rs, "main.py::main")
    assert "app/pipeline.py::Pipeline.run" in t  # pipe.run(...)
    assert "app/pipeline.py::Pipeline.describe" in t


def test_self_attr_typed_by_constructor(ix, rs):
    t = targets(ix, rs, "app/pipeline.py::Pipeline.run")
    assert "app/model/base.py::Base.run" in t  # self.worker.run -> inherited


def test_property_returning_callable(ix, rs):
    t = targets(ix, rs, "app/pipeline.py::Pipeline.run")
    assert "app/model/workers.py::Scorer.__call__" in t  # self.scorer(out)


def test_self_attr_instance_call(ix, rs):
    t = targets(ix, rs, "app/model/workers.py::SimpleWorker.step")
    assert "app/model/workers.py::Scorer.__call__" in t  # self.extra(y)
    assert "app/model/workers.py::SimpleWorker.fallback" in t


def test_super_call(ix, rs):
    t = targets(ix, rs, "app/model/workers.py::SimpleWorker.prepare")
    assert "app/model/base.py::Base.prepare" in t


@pytest.mark.parametrize("self_cls, expected", [
    (None, "app/model/base.py::Base.step"),
    ("app/model/workers.py::SimpleWorker", "app/model/workers.py::SimpleWorker.step"),
])
def test_self_override_picks_concrete_method(ix, rs, self_cls, expected):
    t = targets(ix, rs, "app/model/base.py::Base.run", self_cls)
    assert expected in t


def test_incompatible_self_override_is_ignored(ix, rs):
    t = targets(ix, rs, "app/model/base.py::Base.run", "app/pipeline.py::Pipeline")
    assert "app/model/base.py::Base.step" in t


def test_unique_method_name_is_inferred(ix, rs):
    t = targets(ix, rs, "app/pipeline.py::Pipeline.describe")
    assert t == {"app/model/workers.py::Scorer.score_unique_name": INFERRED}
