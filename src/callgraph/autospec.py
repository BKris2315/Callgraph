"""
Automatic spec: sensible views for a repository nobody has described.

1. **What to parse** (:func:`detect_include`): ``src/`` when it holds the code, otherwise every
   top-level folder and ``.py`` file with Python in it, minus tests, docs, examples, build output
   and virtual environments.
2. **Groups** (:func:`group_key`): one per top-level package. When nearly everything lives in one
   package, one per sub-package or module inside it instead, so a single-package library still
   gets several views.
3. **Entry points**: public functions and methods that nothing in the repository calls (plus any
   ``main``), ranked by how much in-repo code they reach. Names like ``main``, ``cli`` and ``run``,
   and anything in ``__main__.py`` / ``cli.py``, come first. Each group's top ``MAX_ROOTS`` become
   that view's roots.
4. **Views**: one per group with at least one entry point that reaches something, walked three
   calls deep through the group's whole top-level package (all of the source for top-level
   scripts), so a view follows its entry points wherever they lead. Each view "owns" its group's
   files (``owns``), which the overview uses for its arrows; each group gets its own colour.

Everything here is a heuristic: if the views are not the ones you want, run ``callgraph spec`` and
edit the printed spec.
"""

import os
import re
from collections import Counter, deque
from typing import Dict, List, Tuple

from .graph import CallGraph
from .index import Index

COLORS = ("teal", "indigo", "amber", "red", "purple", "blue", "olive", "rose", "taupe", "slate")
SKIP_DIRS = {"tests", "test", "testing", "docs", "doc", "examples", "example", "benchmarks", "build",
             "dist", "node_modules", "venv", "env", "site-packages", "__pycache__"}
SKIP_FILES = {"setup.py", "conftest.py", "noxfile.py"}
PRIORITY_NAMES = {"main", "cli", "run", "app", "serve", "entrypoint", "__call__"}
PRIORITY_FILES = {"__main__.py", "cli.py", "main.py", "app.py"}
DEPTH, REACH_DEPTH, MAX_NODES = 3, 3, 80


def _has_python(path: str) -> bool:
    if os.path.isfile(path):
        return path.endswith(".py")
    for d, dirs, files in os.walk(path):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith(".")]
        if any(f.endswith(".py") for f in files):
            return True
    return False


def detect_include(root: str) -> List[str]:
    """Folders and files to parse, relative to ``root``."""
    src = os.path.join(root, "src")
    if os.path.isdir(src) and _has_python(src):
        return ["src"]
    out = []
    for name in sorted(os.listdir(root)):
        if name.startswith(".") or name in SKIP_DIRS or name in SKIP_FILES:
            continue
        full = os.path.join(root, name)
        if (os.path.isdir(full) or name.endswith(".py")) and _has_python(full):
            out.append(name)
    return out


def _strip_src(path: str) -> Tuple[str, str]:
    return ("src/", path[4:]) if path.startswith("src/") else ("", path)


def group_key(path: str, level: int) -> Tuple[str, str]:
    """``(group name, path prefix)`` of a file at grouping ``level`` (1 = top-level package)."""
    prefix, rest = _strip_src(path)
    parts = rest.split("/")
    if len(parts) <= level:  # a module directly at this level: the file is its own group
        return rest, prefix + rest
    name = "/".join(parts[:level])
    return name, prefix + name + "/"


def _choose_level(ix: Index) -> int:
    counts = Counter(group_key(f.path, 1)[0] for f in ix.funcs.values())
    if not counts:
        return 1
    top, n = counts.most_common(1)[0]
    deeper = {group_key(f.path, 2)[0] for f in ix.funcs.values() if group_key(f.path, 1)[0] == top}
    return 2 if n >= 0.8 * sum(counts.values()) and len(deeper) > 1 else 1


def _reach(graph: CallGraph, fid: str) -> int:
    seen, q = {fid}, deque([(fid, 0)])
    while q:
        cur, d = q.popleft()
        if d >= REACH_DEPTH:
            continue
        for e in graph.callees(cur):
            if e.callee not in seen:
                seen.add(e.callee)
                q.append((e.callee, d + 1))
    return len(seen) - 1


def _is_entry_candidate(ix: Index, graph: CallGraph, fid: str) -> bool:
    f = ix.funcs[fid]
    if f.name == "main":
        return True
    if f.name.startswith("_") and f.name != "__call__":
        return False
    if f.cls and Index.is_property(f):
        return False
    return not graph.callers(fid)


def fill(spec, ix: Index, graph: CallGraph, repo_name: str) -> None:
    """Complete an automatic ``spec`` in place: ``VIEWS``, ``COLOR_RULES``, ``TITLE``, ``SEARCH_EXAMPLE``."""
    spec.TITLE = spec.TITLE or repo_name
    level = _choose_level(ix)
    groups: Dict[str, Tuple[str, List[Tuple[tuple, str]]]] = {}
    for fid, f in ix.funcs.items():
        if not _is_entry_candidate(ix, graph, fid):
            continue
        reach = _reach(graph, fid)
        if reach == 0:
            continue
        name, prefix = group_key(f.path, level)
        prio = int(f.name in PRIORITY_NAMES or os.path.basename(f.path) in PRIORITY_FILES)
        groups.setdefault(name, (prefix, []))[1].append(((-prio, -reach, f.path, f.lineno), fid))

    views, rules = [], []
    for i, name in enumerate(sorted(groups)):
        prefix, cands = groups[name]
        roots = [fid for _, fid in sorted(cands)[: spec.MAX_ROOTS]]
        owns = rf"^{re.escape(prefix)}" + ("" if prefix.endswith("/") else "$")
        _, top = group_key(prefix.rstrip("/"), 1)  # the top-level package this group sits in
        include = rf"^{re.escape(top)}" if top.endswith("/") else r"."  # top-level script: everything
        color = COLORS[i % len(COLORS)]
        views.append({"id": re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or f"view{i}",
                      "title": name, "tag": f"{len(roots)} entry point{'s' if len(roots) != 1 else ''}",
                      "color": f"--c-{color}", "roots": roots, "include": [include], "owns": [owns],
                      "depth": DEPTH, "max_nodes": MAX_NODES})
        rules.append((owns, color))
    spec.VIEWS = views
    spec.COLOR_RULES = list(spec.COLOR_RULES) + rules
    names = [ix.funcs[r].name for v in views for r in v["roots"] if not ix.funcs[r].name.startswith("_")]
    if names and spec.SEARCH_EXAMPLE == "main":
        spec.SEARCH_EXAMPLE = names[0]


def render_spec(spec) -> str:
    """The spec as a Python file a user can edit and pass back with ``--spec``."""
    lines = ['"""View spec for callgraph. Edit freely; see `callgraph --help` and the README."""', ""]
    for key in ("TITLE", "SEARCH_EXAMPLE", "INDEX_INCLUDE", "INDEX_EXCLUDE", "SKIP", "COLOR_RULES", "OVERVIEW"):
        lines.append(f"{key} = {getattr(spec, key)!r}")
    lines += ["", "# Each view starts from `roots` and follows calls `depth` levels deep inside the files `include` matches.",
              "VIEWS = ["]
    for v in spec.VIEWS:
        lines.append("    {")
        for k, val in v.items():
            lines.append(f"        {k!r}: {val!r},")
        lines.append("    },")
    lines.append("]")
    return "\n".join(lines) + "\n"
