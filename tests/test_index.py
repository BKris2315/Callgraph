from callgraph import Index

from conftest import MINIREPO


def test_indexes_functions_and_classes(ix):
    assert "main.py::main" in ix.funcs
    assert "app/model/workers.py::SimpleWorker" in ix.classes
    assert "app/model/workers.py::SimpleWorker.step" in ix.funcs
    assert ix.funcs["main.py::main"].doc == "Load the config and run the pipeline."
    assert ix.parse_errors == []


def test_exclude_patterns_skip_files():
    ix = Index(MINIREPO, ["main.py", "app"], [r"^app/pipeline\.py$"])
    assert "app/pipeline.py::Pipeline.run" not in ix.funcs
    assert "main.py::main" in ix.funcs


def test_relative_and_star_imports_resolve(ix):
    workers = ix.by_path["app/model/workers.py"]
    assert ix.resolve_name("Base", workers) == "app/model/base.py::Base"
    # `from ..util import log`, where app/util/__init__.py star-imports .io
    assert ix.resolve_name("log", workers) == "app/util/io.py::log"


def test_mro_and_find_method(ix):
    worker = "app/model/workers.py::SimpleWorker"
    assert ix.mro(worker) == [worker, "app/model/base.py::Base"]
    assert ix.find_method(worker, "run") == "app/model/base.py::Base.run"
    assert ix.find_method(worker, "step") == "app/model/workers.py::SimpleWorker.step"
    assert ix.find_method(None, "run") is None


def test_calls_record_conditions_and_loops(ix):
    calls = {c.args: c for c in ix.funcs["main.py::main"].calls}
    assert calls[""].conds == ["debug"]  # pipe.describe()
    assert calls["batch"].loops == ["for batch in cfg['batches']"]


def test_find_accepts_several_spellings(ix):
    target = "app/pipeline.py::Pipeline.run"
    assert ix.find(target) == [target]
    assert ix.find("Pipeline.run") == [target]
    assert ix.find("app.pipeline.Pipeline.run") == [target]
    assert "app/model/base.py::Base.run" in ix.find("run")


def test_func_at_finds_innermost(ix):
    step = ix.funcs["app/model/workers.py::SimpleWorker.step"]
    assert ix.func_at("app/model/workers.py", step.lineno + 1) == step.id


def test_body_hash_is_stable(ix):
    again = Index(MINIREPO, ["main.py", "app"])
    assert again.funcs["main.py::main"].body_hash == ix.funcs["main.py::main"].body_hash


def test_src_layout_absolute_imports(tmp_path):
    """``src/pkg/b.py`` doing ``from pkg.a import f`` resolves (src/ is not part of the module name)."""
    pkg = tmp_path / "src" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("def f():\n    pass\n")
    (pkg / "b.py").write_text("from pkg.a import f\nimport pkg.a as amod\n\ndef g():\n    f()\n    amod.f()\n")
    ix = Index(str(tmp_path), ["src"])
    assert "pkg.a" in ix.modules and ix.lookup("pkg.a.f") == "src/pkg/a.py::f"
    from callgraph import Resolver
    rs, g = Resolver(ix), ix.funcs["src/pkg/b.py::g"]
    assert {t for c in g.calls for t, _ in rs.resolve_call(c, g)} == {"src/pkg/a.py::f"}
