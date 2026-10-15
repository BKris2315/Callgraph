"""
Call-tree tracing on a :class:`~callgraph.graph.CallGraph`.

``trace(graph, root)`` follows calls *down* from a function (what it runs, in order);
``trace(graph, root, up=True)`` follows them *up* (who reaches it). A function already shown
higher in the tree is printed once more, marked ``(seen above)``, and not expanded again, so
recursion and shared helpers never blow the tree up.

Example output::

    Engine.run  src/pkg/engine.py:40
    ├─ 1 Loader.read  src/pkg/io/loader.py:12
    │  └─ 1 Cache.get  src/pkg/io/cache.py:30
    ├─ 2 Validator.check  src/pkg/validate.py:21  [if self.strict]
    └─ 3 Writer.write  src/pkg/io/writer.py:55
"""

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set

from .graph import CallGraph, Edge, short_label


@dataclass
class TraceNode:
    """One function in a traced call tree."""

    fid: str
    edge: Optional[Edge] = None  # the call that led here (None for the root)
    order: Optional[int] = None  # position among the parent's calls, 1-based
    children: List["TraceNode"] = field(default_factory=list)
    seen: bool = False  # already expanded elsewhere in the tree
    cut: bool = False  # has more calls, but the depth limit was reached
    outside: bool = False  # outside the include patterns: shown, not expanded

    def to_dict(self, graph: CallGraph) -> dict:
        o = graph.ix.obj(self.fid)
        d = {"id": self.fid, "label": short_label(graph.ix, self.fid), "loc": graph.ix.loc(self.fid)}
        if self.edge is not None:
            d.update(order=self.order, call_line=self.edge.line, args=self.edge.args,
                     condition=self.edge.condition, loop=self.edge.loop, inferred=self.edge.inferred)
        for flag in ("seen", "cut", "outside"):
            if getattr(self, flag):
                d[flag] = True
        if self.children:
            d["children"] = [c.to_dict(graph) for c in self.children]
        return d


def trace(graph: CallGraph, root: str, depth: int = 3, up: bool = False,
          include: Iterable[str] = (), exclude: Iterable[str] = ()) -> TraceNode:
    """Breadth-limited call tree from ``root``.

    Args:
        graph: the call graph to walk.
        root: function id (``path::Qual.name``).
        depth: levels below (or above) the root to show.
        up: follow callers instead of callees.
        include: path regexes; functions in other files are shown but not expanded. Empty = all.
        exclude: id regexes; matching functions are left out entirely.
    """
    inc = [re.compile(p) for p in include]
    exc = [re.compile(p) for p in exclude]
    expanded: Set[str] = set()

    def inside(fid: str) -> bool:
        return not inc or any(p.search(graph.ix.obj(fid).path) for p in inc)

    def build(node: TraceNode, level: int) -> None:
        expanded.add(node.fid)
        edges = graph.callers(node.fid) if up else graph.callees(node.fid)
        edges = [e for e in edges if not any(p.search(e.caller if up else e.callee) for p in exc)]
        if not edges:
            return
        if level >= depth:
            node.cut = True
            return
        for i, e in enumerate(edges, 1):
            fid = e.caller if up else e.callee
            child = TraceNode(fid, e, None if up else i)
            node.children.append(child)
            if fid in expanded:
                child.seen = True
            elif not inside(fid):
                child.outside = True
            else:
                build(child, level + 1)

    root_node = TraceNode(root)
    build(root_node, 0)
    return root_node


def format_tree(graph: CallGraph, node: TraceNode, show_args: bool = False, show_loc: bool = True) -> str:
    """Render a traced tree with box-drawing characters, one function per line."""
    lines: List[str] = []

    def line_for(n: TraceNode) -> str:
        o = graph.ix.obj(n.fid)
        text = (f"{n.order} " if n.order is not None else "") + short_label(graph.ix, n.fid)
        if show_loc:
            text += f"  {graph.ix.loc(n.fid)}"
        if n.edge is not None:
            if n.edge.loop:
                text += f"  [{n.edge.loop}]"
            if n.edge.condition:
                text += f"  [if {n.edge.condition}]"
            if n.edge.inferred:
                text += "  [inferred by name]"
            if show_args:
                text += f"  ({n.edge.args})"
        if n.seen:
            text += "  (seen above)"
        if n.outside:
            text += "  (outside --include)"
        if n.cut:
            text += "  …"
        return text

    def walk(n: TraceNode, prefix: str, last: bool, top: bool) -> None:
        lines.append(line_for(n) if top else f"{prefix}{'└─ ' if last else '├─ '}{line_for(n)}")
        child_prefix = "" if top else prefix + ("   " if last else "│  ")
        for i, c in enumerate(n.children):
            walk(c, child_prefix, i == len(n.children) - 1, False)

    walk(node, "", True, True)
    return "\n".join(lines)
