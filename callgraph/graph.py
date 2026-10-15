"""
The whole-repository call graph: every resolved call between two indexed functions.

This is the "list the connections" layer. It has no notion of views or pages; the page builder
(:mod:`callgraph.views`) walks the resolver itself because it needs to override what ``self`` is
per view, while ``trace`` and ``edges`` use this precomputed graph.
"""

import os
from dataclasses import dataclass, replace
from typing import Dict, Iterable, Iterator, List, Optional, Pattern

from .index import Index
from .symbols import TOPLEVEL
from .resolver import INFERRED, Resolver
from .symbols import Call


def short_label(ix: Index, oid: str) -> str:
    """``Class.method`` for methods, the bare name for module-level functions, the file name for a
    module's top-level code (a script or notebook)."""
    o = ix.obj(oid)
    if o.qual == TOPLEVEL:
        return os.path.basename(o.path)
    parts = o.qual.split(".")
    return ".".join(parts[-2:]) if len(parts) > 1 else parts[0]


def describe_loop(loop: str) -> str:
    """``for x in xs`` -> ``per x in xs · ``, ``while cond`` -> ``in loop while cond · ``, "" -> ""."""
    if not loop:
        return ""
    return f"per {loop[4:]} · " if loop.startswith("for ") else f"in loop {loop} · "


def describe_call(call: Call, target_label: str, inferred: bool = False) -> str:
    """What is passed along an edge, e.g. ``per item in items · write(item, path)``."""
    data = describe_loop(call.loops[-1] if call.loops else "") + f"{target_label.split('.')[-1]}({call.args})"
    return data + ("  [target inferred by method name]" if inferred else "")


def describe_condition(call: Call, limit: int = 120) -> str:
    """The ``if`` / ``except`` conditions around a call, joined with ``and``."""
    cond = " and ".join(call.conds)
    return cond if len(cond) <= limit else cond[: limit - 1] + "…"


@dataclass(frozen=True)
class Edge:
    """One call site: ``caller`` calls ``callee`` at ``line``."""

    caller: str
    callee: str
    line: int
    col: int
    args: str
    condition: str  # "" when unconditional
    loop: str  # innermost enclosing loop, "" when none
    inferred: bool  # target guessed from a unique method name

    @property
    def conditional(self) -> bool:
        return bool(self.condition)

    def to_dict(self) -> dict:
        return {"caller": self.caller, "callee": self.callee, "line": self.line, "args": self.args,
                "condition": self.condition, "loop": self.loop, "inferred": self.inferred}


class CallGraph:
    """Resolved calls between every pair of indexed functions.

    Args:
        index: the parsed source tree.
        resolver: call resolver; one is created when omitted.
        skip: regexes; callees whose id matches are left out (noise such as logging helpers).
    """

    def __init__(self, index: Index, resolver: Optional[Resolver] = None, skip: Iterable[Pattern] = ()) -> None:
        self.ix = index
        self.rs = resolver or Resolver(index)
        self.skip = list(skip)
        self._out: Dict[str, List[Edge]] = {}
        self._in: Dict[str, List[Edge]] = {}
        self.unresolved = 0
        for fid, f in index.funcs.items():
            for call in sorted(f.calls, key=lambda c: (c.lineno, c.col)):
                targets = self.rs.resolve_call(call, f)
                if not targets:
                    self.unresolved += 1
                for tid, note in targets:
                    if any(p.search(tid) for p in self.skip):
                        continue
                    e = Edge(fid, tid, call.lineno, call.col, call.args, describe_condition(call),
                             call.loops[-1] if call.loops else "", note == INFERRED)
                    self._out.setdefault(fid, []).append(e)
                    self._in.setdefault(tid, []).append(e)

    def callees(self, fid: str, unique: bool = True) -> List[Edge]:
        """Calls made by ``fid`` in source order; with ``unique`` only the first call to each target.

        A merged edge counts as unconditional if any of its call sites is.
        """
        edges = self._out.get(fid, [])
        return self._merge(edges, key=lambda e: e.callee) if unique else list(edges)

    def callers(self, fid: str, unique: bool = True) -> List[Edge]:
        """Calls into ``fid``, sorted by caller; with ``unique`` one edge per caller."""
        edges = sorted(self._in.get(fid, []), key=lambda e: (e.caller, e.line))
        return self._merge(edges, key=lambda e: e.caller) if unique else edges

    @staticmethod
    def _merge(edges: List[Edge], key) -> List[Edge]:
        first: Dict[str, Edge] = {}
        for e in edges:
            k = key(e)
            if k not in first:
                first[k] = e
            elif not e.condition and first[k].condition:
                first[k] = replace(first[k], condition="")
        return list(first.values())

    def edges(self) -> Iterator[Edge]:
        """Every call site, grouped by caller in id order."""
        for fid in sorted(self._out):
            yield from self._out[fid]

    def __len__(self) -> int:
        return sum(len(v) for v in self._out.values())
