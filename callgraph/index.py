"""
Static index of a Python source tree: modules, classes, functions and the calls inside them.

Nothing is imported or executed; every file is parsed with :mod:`ast`. Jupyter notebooks are read
as modules too (their code cells, see :mod:`callgraph.notebooks`), and every module's top-level
code (a script's body, a notebook's cells, ``if __name__ == "__main__":``) becomes a
pseudo-function named :data:`~callgraph.symbols.TOPLEVEL`, so calls made there are part of the graph. The index answers
*name* questions (what does ``Foo`` refer to in this module, what are the bases of a class,
which method does ``Foo.bar`` resolve to through the MRO). Working out what a *call* targets
is the job of :class:`callgraph.resolver.Resolver`, which builds on this.
"""

import ast
import hashlib
import os
import re
import sys
from typing import Dict, Iterable, List, Optional, Tuple

from .notebooks import cell_of, notebook_source
from .symbols import TOPLEVEL, Call, Func, Klass, Module, doc_summary, node_text

SOURCE_SUFFIXES = (".py", ".ipynb")
_STDLIB = set(getattr(sys, "stdlib_module_names", ()))


class CallCollector(ast.NodeVisitor):
    """Collects the calls in one function body, with the conditions and loops around each.

    Nested functions are walked as part of their parent (their calls belong to it); nested
    classes are skipped because the index records them on their own.
    """

    def __init__(self) -> None:
        self.calls: List[Call] = []
        self.conds: List[str] = []
        self.loops: List[str] = []

    def _visit_block(self, stmts, cond: Optional[str] = None) -> None:
        if cond is not None:
            self.conds.append(cond)
        for s in stmts:
            self.visit(s)
        if cond is not None:
            self.conds.pop()

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        test = node_text(node.test, 70)
        self._visit_block(node.body, test)
        if node.orelse:
            self._visit_block(node.orelse, f"not ({test})")

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        self.loops.append(f"while {node_text(node.test, 60)}")
        self._visit_block(node.body)
        self.loops.pop()
        self._visit_block(node.orelse)

    def visit_For(self, node) -> None:
        self.visit(node.iter)
        self.loops.append(f"for {node_text(node.target, 30)} in {node_text(node.iter, 50)}")
        self._visit_block(node.body)
        self.loops.pop()
        self._visit_block(node.orelse)

    visit_AsyncFor = visit_For

    def visit_Try(self, node) -> None:
        self._visit_block(node.body)
        for h in node.handlers:
            self._visit_block(h.body, f"except {node_text(h.type, 40) if h.type else ''}".strip())
        self._visit_block(node.orelse)
        self._visit_block(node.finalbody)

    visit_TryStar = visit_Try

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.visit(node.test)
        test = node_text(node.test, 60)
        self.conds.append(test)
        self.visit(node.body)
        self.conds.pop()
        self.conds.append(f"not ({test})")
        self.visit(node.orelse)
        self.conds.pop()

    def visit_ClassDef(self, node) -> None:
        return

    def visit_Call(self, node: ast.Call) -> None:
        parts = [node_text(a, 40) for a in node.args]
        parts += [f"{k.arg}={node_text(k.value, 30)}" if k.arg else f"**{node_text(k.value, 30)}" for k in node.keywords]
        args = ", ".join(parts)
        if len(args) > 110:
            args = args[:109] + "…"
        self.calls.append(Call(node.func, node.lineno, node.col_offset, args, list(self.conds), list(self.loops)))
        self.generic_visit(node)


class Index:
    """Every module, class and function under ``root`` that matches ``include`` and not ``exclude``.

    Args:
        root: directory to index (a checkout of the repository).
        include: paths relative to ``root``; directories are walked, ``.py`` / ``.ipynb`` files taken as-is.
        exclude: regexes matched against the relative path; matching files are skipped.
    """

    def __init__(self, root: str, include: Iterable[str], exclude: Iterable[str] = ()) -> None:
        self.root = root
        self.modules: Dict[str, Module] = {}  # dotted name -> module
        self.by_path: Dict[str, Module] = {}  # relative path -> module
        self.funcs: Dict[str, Func] = {}
        self.classes: Dict[str, Klass] = {}
        self.methods_by_name: Dict[str, List[str]] = {}  # method name -> every method with that name
        self.subclasses: Dict[str, List[str]] = {}  # class id -> direct in-repo subclasses
        self.parse_errors: List[str] = []
        self._mro: Dict[str, List[str]] = {}
        self._suffix: Dict[str, str] = {}  # unique trailing part of a dotted module name -> module name
        self._load(list(include), [re.compile(x) for x in exclude])
        self._link()

    # ------------------------------------------------------------------ loading
    @staticmethod
    def _modname(path: str) -> str:
        """Dotted module name as the code imports it: ``src/pkg/mod.py`` -> ``pkg.mod`` (src layout)."""
        path = os.path.splitext(path.replace(os.sep, "/"))[0]
        if path.startswith("src/"):
            path = path[4:]
        mod = path.replace("/", ".")
        return mod[: -len(".__init__")] if mod.endswith(".__init__") else mod

    def _load(self, include: List[str], exclude) -> None:
        paths = []
        for inc in include:
            full = os.path.join(self.root, inc)
            if os.path.isfile(full) and inc.endswith(SOURCE_SUFFIXES):
                paths.append(inc)
            elif os.path.isdir(full):
                for d, dirs, files in os.walk(full):
                    # hidden folders (.git, .venv, .tox, ...) and installed packages are never source
                    dirs[:] = [x for x in dirs if not x.startswith(".") and x not in ("__pycache__", "node_modules", "site-packages")]
                    paths += [os.path.relpath(os.path.join(d, f), self.root) for f in files if f.endswith(SOURCE_SUFFIXES)]
        for rel in sorted(set(paths)):
            rel = rel.replace(os.sep, "/")
            if any(p.search(rel) for p in exclude):
                continue
            notebook = rel.endswith(".ipynb")
            cells, doc = [], ""
            try:
                with open(os.path.join(self.root, rel), encoding="utf-8") as fh:
                    text = fh.read()
                if notebook:
                    text, cells, skipped, doc = notebook_source(text)
                    if skipped:
                        self.parse_errors.append(f"{rel}: {skipped} cell(s) skipped (not valid Python)")
                tree = ast.parse(text, filename=rel)
            except (SyntaxError, UnicodeDecodeError, ValueError) as e:
                self.parse_errors.append(f"{rel}: {e}")
                continue
            mod = Module(rel, self._modname(rel), tree, doc or doc_summary(tree, 200), source=text,
                         notebook=notebook, cells=cells, driver=self._is_driver(rel, notebook))
            self.modules[mod.name] = mod
            self.by_path[rel] = mod
            self._index_module(mod)

    def _index_module(self, mod: Module) -> None:
        if mod.path.endswith("__init__.py"):
            pkg = mod.name
        else:
            pkg = mod.name.rsplit(".", 1)[0] if "." in mod.name else ""
        for node in ast.walk(mod.tree):
            if isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = pkg.split(".") if pkg else []
                    parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                    base = ".".join(parts + ([base] if base else []))
                for a in node.names:
                    if a.name == "*":
                        mod.stars.append(base)
                    else:
                        mod.imports[a.asname or a.name] = f"{base}.{a.name}" if base else a.name
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.asname:
                        mod.imports[a.asname] = a.name
                    else:
                        mod.imports.setdefault(a.name.split(".")[0], a.name.split(".")[0])
        self._index_body(mod, mod.tree.body, prefix="", cls=None)
        self._index_toplevel(mod)

    def _is_driver(self, rel: str, notebook: bool) -> bool:
        """Notebooks, and scripts that are not part of a package (no ``__init__.py`` next to them)."""
        if notebook:
            return True
        folder = os.path.dirname(rel)
        if rel.startswith("src/") or os.path.basename(rel) == "__init__.py":
            return False
        return not os.path.exists(os.path.join(self.root, folder, "__init__.py"))

    def _index_toplevel(self, mod: Module) -> None:
        """Top-level statements with calls in them -> one pseudo-function ``path::<module>``."""
        body = [s for s in mod.tree.body if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom))]
        if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
            body = body[1:]  # the module docstring
        if not any(isinstance(n, ast.Call) for s in body for n in ast.walk(s)):
            return
        extra = {"type_params": []} if "type_params" in ast.FunctionDef._fields else {}  # Python 3.12+
        node = ast.FunctionDef(name=TOPLEVEL, args=ast.arguments(posonlyargs=[], args=[], vararg=None, kwonlyargs=[],
                                                                 kw_defaults=[], kwarg=None, defaults=[]),
                               body=body, decorator_list=[], returns=None, **extra)
        node.lineno, node.col_offset = body[0].lineno, 0
        node.end_lineno, node.end_col_offset = body[-1].end_lineno, body[-1].end_col_offset
        fid = f"{mod.path}::{TOPLEVEL}"
        f = Func(fid, mod.path, mod.name, TOPLEVEL, TOPLEVEL, None, node.lineno, mod.doc, [], node)
        col = CallCollector()
        for s in body:
            col.visit(s)
        f.calls = col.calls
        f.body_hash = hashlib.sha1(ast.dump(node, include_attributes=False).encode()).hexdigest()[:12]
        f.source = "\n".join(ast.get_source_segment(mod.source, s) or "" for s in body)
        self._collect_local_types(f)
        self.funcs[fid] = f

    def _index_body(self, mod: Module, body, prefix: str, cls: Optional[Klass]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{node.name}"
                fid = f"{mod.path}::{qual}"
                f = Func(fid, mod.path, mod.name, qual, node.name, cls.id if cls else None, node.lineno,
                         doc_summary(node), node.decorator_list, node)
                col = CallCollector()
                for s in node.body:
                    col.visit(s)
                f.calls = col.calls
                f.body_hash = hashlib.sha1(ast.dump(node, include_attributes=False).encode()).hexdigest()[:12]
                self._collect_local_types(f)
                self.funcs[fid] = f
                if cls:
                    cls.methods[node.name] = fid
                    self._collect_attr_types(cls, f)
                elif not prefix:
                    mod.defs[node.name] = fid
            elif isinstance(node, ast.ClassDef):
                qual = f"{prefix}{node.name}"
                kid = f"{mod.path}::{qual}"
                k = Klass(kid, mod.path, mod.name, qual, node.name, node.lineno, doc_summary(node),
                          node.bases, node.decorator_list, node)
                self.classes[kid] = k
                if not prefix:
                    mod.defs[node.name] = kid
                for s in node.body:
                    if isinstance(s, ast.Assign):
                        for t in s.targets:
                            if isinstance(t, ast.Name):
                                k.class_vars[t.id] = s.value
                    elif isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name):
                        if s.value is not None:
                            k.class_vars[s.target.id] = s.value
                        k.attr_types.setdefault(s.target.id, s.annotation)
                self._index_body(mod, node.body, prefix=f"{qual}.", cls=k)

    @staticmethod
    def _collect_local_types(f: Func) -> None:
        """Annotated parameters, annotated locals and locals assigned from a call."""
        args = f.node.args
        for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
            if a.annotation is not None:
                f.local_types[a.arg] = a.annotation
        for n in ast.walk(f.node):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        f.local_types.setdefault(t.id, n.value)
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                f.local_types[n.target.id] = n.annotation

    @staticmethod
    def _collect_attr_types(k: Klass, f: Func) -> None:
        """``self.x: T = ...``, ``self.x = Cls(...)`` and ``self.x = typed_local`` inside a method."""
        for n in ast.walk(f.node):
            targets, value, ann = [], None, None
            if isinstance(n, ast.Assign):
                targets, value = n.targets, n.value
            elif isinstance(n, ast.AnnAssign):
                targets, value, ann = [n.target], n.value, n.annotation
            for t in targets:
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                    if ann is not None:
                        k.attr_types[t.attr] = ann
                    elif isinstance(value, ast.Call):
                        k.attr_types.setdefault(t.attr, value)
                    elif isinstance(value, ast.Name) and value.id in f.local_types:
                        k.attr_types.setdefault(t.attr, f.local_types[value.id])

    def _link(self) -> None:
        for fid, f in self.funcs.items():
            if f.cls:
                self.methods_by_name.setdefault(f.name, []).append(fid)
        for kid in self.classes:
            for b in self.base_ids(kid):
                self.subclasses.setdefault(b, []).append(kid)
        counts: Dict[str, List[str]] = {}
        for name in self.modules:
            parts = name.split(".")
            for i in range(1, len(parts)):
                counts.setdefault(".".join(parts[i:]), []).append(name)
        self._suffix = {k: v[0] for k, v in counts.items() if len(v) == 1 and k not in self.modules}
        # A top-level script that other files import (``model.py`` next to ``train.py``) is library code, not a driver.
        imported = set()
        for mod in self.modules.values():
            for dotted in list(mod.imports.values()) + mod.stars:
                parts = dotted.split(".")
                for i in range(len(parts), 0, -1):
                    head = ".".join(parts[:i])
                    target = self.modules.get(head) or (self.modules.get(self._suffix[head]) if head in self._suffix and parts[0] not in _STDLIB else None)
                    if target is not None and target is not mod:
                        imported.add(target.path)
                        break
        for mod in self.modules.values():
            if mod.driver and not mod.notebook and mod.path in imported:
                mod.driver = False

    # ------------------------------------------------------------------ name lookup
    def lookup(self, fq: str, _depth: int = 0) -> Optional[str]:
        """Fully qualified dotted name -> func/class id, following re-exports and star imports."""
        if _depth > 8 or not fq:
            return None
        parts = fq.split(".")
        for i in range(len(parts), 0, -1):
            modname = ".".join(parts[:i])
            if modname in self.modules:
                rest = parts[i:]
                return self._lookup_in_module(self.modules[modname], rest, _depth) if rest else None
        return None

    def _lookup_in_module(self, mod: Module, rest: List[str], depth: int) -> Optional[str]:
        head, tail = rest[0], rest[1:]
        target = mod.defs.get(head)
        if target is None and head in mod.imports:
            target = self.lookup(mod.imports[head], depth + 1)
            if target is None and tail:
                return self.lookup(".".join([mod.imports[head]] + tail), depth + 1)
        if target is None:
            for star in mod.stars:
                target = self.lookup(f"{star}.{head}", depth + 1)
                if target:
                    break
        if target is None:
            return None
        if not tail:
            return target
        if target in self.classes and len(tail) == 1:
            return self.find_method(target, tail[0])
        return None

    def lookup_from(self, fq: str, mod: Module) -> Optional[str]:
        """:meth:`lookup` as seen from ``mod``. Notebooks and scripts often reach the library through
        ``sys.path`` tricks (``import data`` for ``lab/data.py``), so for them a name that matches the
        unique tail of an in-repo module name is accepted, unless it is a standard-library module."""
        r = self.lookup(fq)
        if r or not mod.driver or not fq:
            return r
        parts = fq.split(".")
        for i in range(len(parts), 0, -1):
            head = ".".join(parts[:i])
            if head in self._suffix and parts[0] not in _STDLIB:
                return self.lookup(".".join([self._suffix[head]] + parts[i:]))
        return None

    def resolve_name(self, name: str, mod: Module) -> Optional[str]:
        """What a bare name refers to inside ``mod``: a local def, an import or a star import."""
        if name in mod.defs:
            return mod.defs[name]
        if name in mod.imports:
            return self.lookup_from(mod.imports[name], mod)
        for star in mod.stars:
            r = self.lookup(f"{star}.{name}")
            if r:
                return r
        return None

    def resolve_expr_class(self, expr: Optional[ast.expr], mod: Module) -> Optional[str]:
        """An annotation or class expression -> class id.

        Unwraps ``Optional[...]``, ``Union[...]``, ``Type[...]``, ``A | B`` and string annotations;
        the first member that is an in-repo class wins.
        """
        if expr is None:
            return None
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            try:
                expr = ast.parse(expr.value, mode="eval").body
            except SyntaxError:
                return None
        if isinstance(expr, ast.Subscript):
            if node_text(expr.value).split(".")[-1] in ("Optional", "Union", "Type", "ClassVar", "Annotated"):
                inner = expr.slice
                for e in inner.elts if isinstance(inner, ast.Tuple) else [inner]:
                    r = self.resolve_expr_class(e, mod)
                    if r:
                        return r
            return None
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
            return self.resolve_expr_class(expr.left, mod) or self.resolve_expr_class(expr.right, mod)
        if isinstance(expr, ast.Name):
            r = self.resolve_name(expr.id, mod)
            return r if r in self.classes else None
        if isinstance(expr, ast.Attribute):
            dotted = node_text(expr, 200)
            first = dotted.split(".")[0]
            if first in mod.imports:
                r = self.lookup_from(mod.imports[first] + dotted[len(first):], mod)
                return r if r in self.classes else None
        return None

    def resolve_expr_classes(self, expr: Optional[ast.expr], mod: Module) -> List[str]:
        """Every class an annotation can name: all members of ``Union[...]`` / ``A | B``."""
        if isinstance(expr, ast.Subscript) and node_text(expr.value).split(".")[-1] in ("Union", "Optional"):
            inner = expr.slice
            elts = inner.elts if isinstance(inner, ast.Tuple) else [inner]
            return [k for e in elts for k in self.resolve_expr_classes(e, mod)]
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
            return self.resolve_expr_classes(expr.left, mod) + self.resolve_expr_classes(expr.right, mod)
        k = self.resolve_expr_class(expr, mod)
        return [k] if k else []

    # ------------------------------------------------------------------ classes
    def base_ids(self, kid: str) -> List[str]:
        """In-repo base classes of ``kid`` (external bases such as ``nn.Module`` are dropped)."""
        k = self.classes[kid]
        mod = self.modules[k.module]
        out = []
        for b in k.bases:
            r = self.resolve_expr_class(b, mod)
            if r and r != kid:
                out.append(r)
        return out

    def mro(self, kid: str) -> List[str]:
        """``kid`` followed by its in-repo bases, depth first (close enough to Python's MRO here)."""
        if kid not in self._mro:
            seen, order = set(), []

            def walk(c):
                if c in seen:
                    return
                seen.add(c)
                order.append(c)
                for b in self.base_ids(c):
                    walk(b)

            walk(kid)
            self._mro[kid] = order
        return self._mro[kid]

    def find_method(self, kid: Optional[str], name: str, skip: int = 0) -> Optional[str]:
        """The method ``name`` that an instance of ``kid`` would run, or ``None``."""
        if not kid:
            return None
        for c in self.mro(kid)[skip:]:
            m = self.classes[c].methods.get(name)
            if m:
                return m
        return None

    def class_var(self, kid: str, name: str) -> Tuple[Optional[str], Optional[ast.expr]]:
        """``(defining class id, value expression)`` of a class attribute, looked up through the MRO."""
        for c in self.mro(kid):
            v = self.classes[c].class_vars.get(name)
            if v is not None:
                return c, v
        return None, None

    @staticmethod
    def is_property(f: Func) -> bool:
        return any(node_text(d) in ("property", "cached_property", "functools.cached_property") for d in f.decorators)

    # ------------------------------------------------------------------ lookups by location / name
    def loc(self, oid: str, line: Optional[int] = None) -> str:
        """Where something is, for people: ``path:line``, or ``notebook.ipynb:cell 3`` in notebooks."""
        o = self.obj(oid)
        line = line or o.lineno
        mod = self.by_path.get(o.path)
        if mod is not None and mod.notebook:
            return f"{o.path}:cell {cell_of(mod.cells, line)}"
        return f"{o.path}:{line}"

    def obj(self, oid: str):
        """The :class:`Func` or :class:`Klass` with this id, or ``None``."""
        return self.funcs.get(oid) or self.classes.get(oid)

    def by_ref(self, ref: str) -> Optional[str]:
        """``'path::Qual.name'`` -> id, if it exists in this tree."""
        return ref if ref in self.funcs or ref in self.classes else None

    def func_at(self, path: str, line: int) -> Optional[str]:
        """The innermost function or class whose definition starts at, or spans, ``line``."""
        best, best_start = None, -1
        for table in (self.funcs, self.classes):
            for i, o in table.items():
                if o.path != path:
                    continue
                end = getattr(o.node, "end_lineno", o.node.lineno)
                if o.node.lineno <= line <= end and o.node.lineno > best_start:
                    best, best_start = i, o.node.lineno
        return best

    def find(self, query: str) -> List[str]:
        """Ids matching a user-typed name, best matches first.

        Accepts a file name or path of a script/notebook (its top-level code), a full id (``path::Qual.name``), a dotted module path (``pkg.engine.Engine.run``),
        a qualified name (``Model.forward``) or a bare name (``forward``).
        """
        if query in self.funcs or query in self.classes:
            return [query]
        # a notebook or script by file name or path: its top-level code
        files = [f"{p}::{TOPLEVEL}" for p in self.by_path if p == query or os.path.basename(p) == query]
        files = [f for f in files if f in self.funcs]
        if files:
            return sorted(files)
        hit = self.lookup(query)
        if hit:
            return [hit]
        exact, suffix = [], []
        for table in (self.funcs, self.classes):
            for oid, o in table.items():
                if o.qual == query:
                    exact.append(oid)
                elif o.qual.endswith("." + query) or oid.endswith(query):
                    suffix.append(oid)
        return sorted(exact) + sorted(suffix)
