"""
Page views built from the index.

:func:`build_page` is the entry point: it completes an automatic spec if needed, builds every view
the spec lists and, when there are several, an overview of them, and returns the page data
together with warnings.
"""

from dataclasses import dataclass, field
from typing import List

from .. import autospec
from ..graph import CallGraph
from ..index import Index
from ..overlay import Overlay
from ..resolver import Resolver
from ..source import Snapshot
from .builder import STAGES, PageBuilder
from .overview import build_overview
from .pipeline import build_pipeline_view

__all__ = ["Page", "build_page", "PageBuilder", "STAGES"]


@dataclass
class Page:
    """Result of :func:`build_page`."""

    data: dict  # what the HTML page renders (see views/builder.py for the schema)
    spec: object  # the spec used, completed when it was automatic
    warnings: List[str] = field(default_factory=list)

    @property
    def node_count(self) -> int:
        return sum(len(v["nodes"]) for v in self.data["views"].values())

    @property
    def edge_count(self) -> int:
        return sum(len(v["edges"]) for v in self.data["views"].values())


def build_page(ix: Index, spec, overlay: Overlay, snap: Snapshot, rs: Resolver = None, repo_name: str = "") -> Page:
    """Build all views described by ``spec`` for the indexed snapshot.

    An automatic spec (no ``VIEWS``) is completed first from the code itself; ``repo_name`` is
    then used as the title.
    """
    rs = rs or Resolver(ix)
    if spec.AUTO and not spec.VIEWS:
        autospec.fill(spec, ix, CallGraph(ix, rs), repo_name)
    pb = PageBuilder(ix, rs, spec, overlay, snap)
    view_ids = [build_pipeline_view(pb, s) for s in spec.VIEWS]
    if not view_ids:
        pb.warnings.append("no views: no entry points found (pass --spec with VIEWS, see `callgraph spec`)")
    if spec.OVERVIEW and len(view_ids) > 1:
        build_overview(pb, view_ids)
        view_ids.insert(0, "overview")
    data = pb.finish([{"title": "Views", "views": view_ids}])
    return Page(data, spec, list(dict.fromkeys(pb.warnings)))
