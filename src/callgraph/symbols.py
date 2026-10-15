"""
Plain data records produced by the indexer, plus small AST text helpers.

These are deliberately dumb containers: parsing lives in :mod:`callgraph.index`, call
resolution in :mod:`callgraph.resolver`.

Ids
---
Every function and class is identified by ``"<repo-relative path>::<Qualified.name>"``,
e.g. ``"src/pkg/engine.py::Engine.run"``. The same string is used as the node
id on the page, as the key in overlay files and as the ``ref`` in view specs.
"""

import ast
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


def node_text(node: Optional[ast.AST], limit: int = 90) -> str:
    """Source-like, single-line text of an AST node, cut to ``limit`` characters."""
    if node is None:
        return ""
    try:
        s = ast.unparse(node)
    except Exception:  # pragma: no cover - malformed nodes
        return ""
    s = re.sub(r"\s+", " ", s)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def doc_summary(node: ast.AST, limit: int = 420) -> str:
    """First paragraph of a docstring, whitespace-collapsed and cut to ``limit`` characters."""
    doc = ast.get_docstring(node) or ""
    para = doc.strip().split("\n\n")[0]
    para = re.sub(r"\s+", " ", para).strip()
    return para if len(para) <= limit else para[: limit - 1] + "…"


def str_literals(node: Optional[ast.expr]) -> List[str]:
    """Every string constant inside an expression (e.g. ``("a", "b")`` -> ``["a", "b"]``)."""
    if node is None:
        return []
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


@dataclass
class Call:
    """One call expression inside a function body."""

    func: ast.expr  # the callee expression, e.g. ``self.writer.write`` in ``self.writer.write(x)``
    lineno: int
    col: int
    args: str  # the arguments as source text, shortened
    conds: List[str]  # enclosing ``if`` / ``except`` / ternary conditions, outermost first
    loops: List[str]  # enclosing ``for`` loops, outermost first


@dataclass
class Func:
    """A function or method."""

    id: str
    path: str
    module: str
    qual: str
    name: str
    cls: Optional[str]  # id of the owning class, for methods
    lineno: int
    doc: str
    decorators: List[ast.expr]
    node: ast.AST
    calls: List[Call] = field(default_factory=list)
    local_types: Dict[str, ast.expr] = field(default_factory=dict)  # name -> annotation or constructor call
    body_hash: str = ""  # changes whenever the function's AST changes


@dataclass
class Klass:
    """A class."""

    id: str
    path: str
    module: str
    qual: str
    name: str
    lineno: int
    doc: str
    bases: List[ast.expr]
    decorators: List[ast.expr]
    node: ast.ClassDef
    methods: Dict[str, str] = field(default_factory=dict)  # method name -> func id
    class_vars: Dict[str, ast.expr] = field(default_factory=dict)  # name -> assigned value
    attr_types: Dict[str, ast.expr] = field(default_factory=dict)  # self.<attr> -> annotation or constructor call


@dataclass
class Module:
    """A parsed ``.py`` file."""

    path: str
    name: str  # dotted module name
    tree: ast.Module
    doc: str
    imports: Dict[str, str] = field(default_factory=dict)  # local name -> fully qualified dotted name
    stars: List[str] = field(default_factory=list)  # modules imported with ``from x import *``
    defs: Dict[str, str] = field(default_factory=dict)  # top-level name -> func/class id
