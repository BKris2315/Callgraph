"""
Shared machinery for building page views: node records, BFS walks, induced edges, final assembly.

Page data model (what ``web/app.js`` renders)::

    {
      "start": "overview",
      "views": {
        "<view id>": {
          "id", "title", "tag", "color", "subtitle"?, "intro"?: [str], "intro_by"?: model name, "notes"?: [str],
          "groups": [{"id", "label", "stage", "desc"?, "kind"?}],   # kind "file": one box per source file
          "nodes":  [{"id", "label", "q", "loc", "desc", "group", "stage"?, "ext"?, "goto"?, "note"?}],
          # "stage" is a colour name from STAGES
          "edges":  [[source, target, order, data, condition], ...]
        }
      },
      "sections": [{"title", "views": [view ids]}],
      "stageColors": {stage: css variable},
      "generated": {"commit", "date", "subject", "at"}
    }

``order`` is the call's position inside the caller (by source line), or ``""`` for edges the
source does not show (e.g. added by hand). ``condition`` is the real ``if`` text, ``""`` when unconditional.
"""

import datetime
import re
from collections import deque
from typing import Callable, Dict, List, Optional, Tuple

from ..graph import describe_call, describe_condition, short_label
from ..index import Index
from ..overlay import Overlay
from ..resolver import INFERRED, Resolver
from ..source import Snapshot

# Colour names map to the --c-<name> variables in web/app.css (light and dark variants).
STAGES = ("slate", "amber", "indigo", "teal", "red", "purple", "olive", "taupe", "blue", "rose")

Root = Tuple[str, str, Optional[str]]  # (function id, group id, class `self` is known to be)


class PageBuilder:
    """Accumulates views for one page.

    Args:
        ix: parsed source tree.
        rs: call resolver over ``ix``.
        spec: loaded view spec (see :mod:`callgraph.specs`).
        overlay: hand-written descriptions (optional).
        snap: the snapshot being described (commit, date, subject, submodules).
    """

    def __init__(self, ix: Index, rs: Resolver, spec, overlay: Overlay, snap: Snapshot) -> None:
        self.ix, self.rs, self.spec, self.overlay, self.snap = ix, rs, spec, overlay, snap
        self.views: Dict[str, dict] = {}
        self.view_roots: Dict[str, str] = {}  # function/class id -> view that starts from it (for "goto")
        self.warnings: List[str] = []
        self._skip = [re.compile(p) for p in spec.SKIP]
        self._stage_rules = [(re.compile(rx), color) for rx, color in spec.COLOR_RULES]

    # ---------------------------------------------------------------- small helpers
    def stage_for(self, path: str) -> str:
        for rx, stage in self._stage_rules:
            if rx.search(path):
                return stage
        return STAGES[0]

    def skipped(self, fid: str) -> bool:
        return any(p.search(fid) for p in self._skip)

    def label(self, oid: str) -> str:
        return short_label(self.ix, oid)

    def resolve_ref(self, ref: str, where: str) -> Optional[str]:
        """A spec ref -> id; a missing ref is a warning, not an error."""
        r = self.ix.by_ref(ref)
        if not r:
            self.warnings.append(f"{where}: {ref} does not exist at this commit")
        return r

    # ---------------------------------------------------------------- views, nodes, edges
    def new_view(self, vid: str, **kw) -> dict:
        """Register an empty view; overlay subtitle / intro / notes are applied when present."""
        text = self.overlay.view(vid)
        v = {"id": vid, "nodes": [], "edges": [], "groups": [], **kw}
        for key in ("subtitle", "intro"):
            if text.get(key):
                v[key] = text[key]
        if text.get("intro") and text.get("model"):
            v["intro_by"] = text["model"]  # the page labels model-written intros
        if text.get("notes"):
            written = text.get("commit")
            stamp = f" (noted at {written[:8]})" if written and written != self.snap.sha else ""
            v["notes"] = [n + stamp for n in text["notes"]]
        self.views[vid] = v
        return v

    def make_node(self, oid: str, group: str, ext: bool = False, self_cls: Optional[str] = None) -> dict:
        """Node record for a function or class. ``_self`` is internal and stripped in :meth:`finish`."""
        obj = self.ix.obj(oid)
        desc, note = self.overlay.describe(oid, obj)
        n = {"id": oid, "label": self.label(oid), "q": f"{obj.module}.{obj.qual}", "loc": self.ix.loc(oid),
             "desc": desc or "(no docstring)", "group": group, "stage": self.stage_for(obj.path), "_self": self_cls}
        if ext:
            n["ext"] = True
        if note:
            n["note"] = note
        return n

    def walk(self, view: dict, roots: List[Root], include: List[str], depth: int, max_nodes: int,
             group_of: Callable[[str, str], str]) -> None:
        """Breadth-first from ``roots``, ``depth`` levels deep.

        Callees whose file matches ``include`` are expanded; others are added as dashed boundary
        nodes (``ext``) and not followed. ``self_cls`` is carried down to methods of the same class
        hierarchy so inherited code resolves ``self.x()`` against the concrete class.
        """
        ix = self.ix
        include_rx = [re.compile(p) for p in include]
        present = {n["id"] for n in view["nodes"]}
        q = deque()
        for rid, grp, self_cls in roots:
            if rid in present:
                continue
            view["nodes"].append(self.make_node(rid, group_of(rid, grp), self_cls=self_cls))
            present.add(rid)
            q.append((rid, 0, grp, self_cls))
        truncated = False
        while q:
            fid, d, grp, self_cls = q.popleft()
            f = ix.funcs.get(fid)
            if not f or d >= depth:
                continue
            for call in sorted(f.calls, key=lambda c: (c.lineno, c.col)):
                for tid, _ in self.rs.resolve_call(call, f, self_cls):
                    if tid in present or self.skipped(tid):
                        continue
                    if len(present) >= max_nodes:
                        truncated = True
                        continue
                    t = ix.funcs[tid]
                    inside = any(p.search(t.path) for p in include_rx)
                    t_self = self_cls if (self_cls and t.cls and t.cls in ix.mro(self_cls)) else None
                    view["nodes"].append(self.make_node(tid, group_of(tid, grp), ext=not inside, self_cls=t_self))
                    present.add(tid)
                    if inside:
                        q.append((tid, d + 1, grp, t_self))
        if truncated:
            self.warnings.append(f"view '{view['id']}' hit its {max_nodes}-function limit; raise max_nodes in the spec to see more")

    def induced_edges(self, view: dict, synthetic: List[list]) -> None:
        """Every resolved call between two nodes of the view, numbered in call order per caller.

        ``synthetic`` edges (calls the source does not show, e.g. dispatch through a registry) are
        appended unless a real call already connects the same pair.
        """
        ix = self.ix
        present = {n["id"] for n in view["nodes"]}
        edges: Dict[Tuple[str, str], list] = {}
        for n in view["nodes"]:
            f = ix.funcs.get(n["id"])
            if not f:
                continue
            for call in sorted(f.calls, key=lambda c: (c.lineno, c.col)):
                for tid, note in self.rs.resolve_call(call, f, n.get("_self")):
                    if tid not in present or tid == n["id"]:
                        continue
                    key = (n["id"], tid)
                    cond = describe_condition(call)
                    if key in edges:  # several call sites: unconditional if any of them is
                        if not cond:
                            edges[key][4] = ""
                        continue
                    edges[key] = [n["id"], tid, call.lineno, describe_call(call, self.label(tid), note == INFERRED), cond]
        by_src: Dict[str, List[list]] = {}
        for e in edges.values():
            by_src.setdefault(e[0], []).append(e)
        out = []
        for lst in by_src.values():
            lst.sort(key=lambda e: e[2])
            out += [[e[0], e[1], i, e[3], e[4]] for i, e in enumerate(lst, 1)]
        seen = {(e[0], e[1]) for e in out}
        for e in synthetic:
            if e[0] in present and e[1] in present and (e[0], e[1]) not in seen:
                out.append(e)
                seen.add((e[0], e[1]))
        view["edges"] = out

    # ---------------------------------------------------------------- assembly
    def finish(self, sections: List[dict]) -> dict:
        """Strip internal fields, add cross-view "goto" links and return the page data."""
        for v in self.views.values():
            for n in v["nodes"]:
                n.pop("_self", None)
                if n.get("stage") is None:
                    n.pop("stage", None)
                tgt = self.view_roots.get(n["id"])
                if tgt and tgt != v["id"] and "goto" not in n:
                    n["goto"] = tgt
        return {
            "start": "overview" if "overview" in self.views else next(iter(self.views), ""),
            "views": self.views,
            "sections": [s for s in sections if s["views"]],
            "stageColors": {k: f"--c-{k}" for k in STAGES},
            "generated": {"commit": self.snap.sha, "date": self.snap.date, "subject": self.snap.subject,
                          "at": datetime.datetime.now().isoformat(timespec="seconds")},
        }
