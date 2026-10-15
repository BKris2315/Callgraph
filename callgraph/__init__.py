"""
callgraph: static call graphs of a Python repository, rendered as an interactive HTML page.

Works on any Python repository: views are chosen automatically from the code's entry points, or
described by hand in a spec file. Nothing from the analysed repository is imported or executed.

Layers, bottom up::

    source     -> a snapshot of the repo on disk (git archive of a commit, or the working tree)
    index      -> modules, classes, functions and the calls inside them (ast)
    resolver   -> which function each call runs (static type/import inference)
    graph      -> every resolved call in the repo; trace -> call trees from one function
    autospec   -> automatic views: source folders, packages, entry points
    views      -> page views (one per entry-point group, plus an overview), optional hand-written overlay
    render     -> one self-contained HTML file

Library use::

    from callgraph import Index, Resolver, CallGraph, trace, format_tree
    ix = Index("/path/to/repo", ["src"])
    g = CallGraph(ix)
    print(format_tree(g, trace(g, "src/pkg/core.py::Engine.run", depth=2)))
"""

from .graph import CallGraph, Edge
from .index import Index
from .overlay import Overlay
from .render import render_html
from .resolver import Resolver
from .source import Snapshot, snapshot
from .specs import load_spec
from .trace import TraceNode, format_tree, trace
from .views import Page, build_page

__version__ = "1.0.0"

__all__ = [
    "CallGraph", "Edge", "Index", "Overlay", "Page", "Resolver", "Snapshot", "TraceNode",
    "build_page", "format_tree", "load_spec", "render_html", "snapshot", "trace",
]
