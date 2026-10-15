"""
Pipeline views: a walk of the call graph from fixed root functions.

Spec entry (one per view in ``spec.PIPELINE_VIEWS``)::

    {
        "id": "core", "title": "Core engine", "tag": "pkg/core", "color": "--c-teal",
        "roots": ["pkg/core/engine.py::Engine.run", ...],   # where the walk starts
        "include": [r"^pkg/", ...],               # files the walk may expand into
        "owns": [r"^pkg/core/"],                  # optional: files that "belong" to this view (overview arrows)
        "depth": 3, "max_nodes": 110,
    }

Nodes are grouped one box per source file. Calls leaving ``include`` become dashed boundary
boxes that are drawn but not followed.
"""

from .builder import PageBuilder


def build_pipeline_view(pb: PageBuilder, spec: dict) -> str:
    """Add the view described by ``spec`` to ``pb`` and return its id."""
    vid = spec["id"]
    v = pb.new_view(vid, title=spec["title"], tag=spec.get("tag", ""), color=spec.get("color", "--accent"))
    roots = [(r, "", None) for r in (pb.resolve_ref(x, vid) for x in spec["roots"]) if r]
    for r, _, _ in roots:
        pb.view_roots.setdefault(r, vid)

    def group_of(fid, _group):
        return "g:" + pb.ix.obj(fid).path

    depth = spec.get("depth", 3)
    pb.walk(v, roots, spec["include"], depth, spec.get("max_nodes", 100), group_of)

    paths = list(dict.fromkeys(n["group"][2:] for n in v["nodes"]))
    v["groups"] = [{"id": "g:" + p, "label": p, "kind": "file", "stage": pb.stage_for(p),
                    "desc": pb.ix.by_path[p].doc if p in pb.ix.by_path else ""} for p in paths]
    pb.induced_edges(v, [])
    facts = (f"Starts from {', '.join(pb.label(r) for r, _, _ in roots)} and follows calls {depth} levels deep. "
             "One box per source file.")
    if not v.get("intro"):
        v["intro"] = [facts]
    elif v.get("intro_by"):  # a model-written intro: keep the plain facts next to it
        v["intro"] = list(v["intro"]) + [facts]
    return vid
