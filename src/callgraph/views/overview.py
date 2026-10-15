"""
The overview view: one box per view, and an arrow wherever one view's code calls into another's.

An arrow A → B counts the functions drawn in view A that belong to view B: they live in the files B
``owns`` (automatic views) or, for hand-written views without ``owns``, the files B walks
(``include``), and not in A's own. Double-clicking a box opens its view.
"""

import re
from typing import List

from .builder import PageBuilder

GROUP = "o_views"


def build_overview(pb: PageBuilder, view_ids: List[str]) -> str:
    """Add the overview of ``view_ids`` to ``pb`` and return its id (``"overview"``)."""
    specs = {s["id"]: s for s in pb.spec.VIEWS}
    v = pb.new_view("overview", title="Overview", tag="all views", color="--accent")
    v["groups"] = [{"id": GROUP, "label": pb.spec.TITLE or "Views", "stage": "slate"}]
    for vid in view_ids:
        view = pb.views[vid]
        roots = [n for n in view["nodes"] if n["id"] in set(specs.get(vid, {}).get("roots", []))]
        color = (view.get("color") or "").replace("--c-", "") or "slate"
        v["nodes"].append({
            "id": "o_" + vid, "label": view["title"], "group": GROUP, "goto": vid, "stage": color,
            "desc": f"{len(view['nodes'])} functions. Starts from: " + (", ".join(n["label"] for n in roots) or "—") + ".",
        })
    owns = {vid: [re.compile(p) for p in specs.get(vid, {}).get("owns") or specs.get(vid, {}).get("include", [])] for vid in view_ids}
    for a in view_ids:
        counts = {}
        for n in pb.views[a]["nodes"]:
            path = n["loc"].rsplit(":", 1)[0]
            if any(p.search(path) for p in owns[a]):
                continue
            for b in view_ids:
                if b != a and any(p.search(path) for p in owns[b]):
                    counts[b] = counts.get(b, 0) + 1
        for b, c in sorted(counts.items(), key=lambda kv: -kv[1]):
            v["edges"].append(["o_" + a, "o_" + b, "", f"{c} function{'s' if c != 1 else ''} of {pb.views[b]['title']} called", ""])

    snap = pb.snap
    intro = list(v.get("intro") or [])
    intro.insert(0, f"Generated from {snap.label} ({snap.date}): {snap.subject}. "
                    "One box per view; an arrow means code in one view calls into the other. Double-click a box to open it.")
    v["intro"] = intro
    return "overview"
