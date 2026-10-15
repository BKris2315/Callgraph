"""
Automatic spec: sensible views for a repository nobody has described.

1. **What to parse** (:func:`detect_include`): every top-level folder and file with Python code
   (``.py`` or Jupyter ``.ipynb``) in it, ``src/`` included, minus tests, docs, examples, build
   output, virtual environments and hidden folders.
2. **Drivers and library**: notebooks, and scripts that are not part of a package, *drive* the
   code (see ``Module.driver``). Everything else is the library.
3. **Driver views** come first: one per folder of drivers (``notebooks/``, ``scripts/``, the files
   at the top level), with every notebook or script whose code calls into the repository as an
   entry point, in file order, walked through all of the source. This is the "which experiment
   uses what" picture.
4. **Library groups** (:func:`group_key`): one per top-level package. When nearly everything lives
   in one package, one per sub-package or module instead, so a single-package library still gets
   several views.
5. **Library entry points**: public functions and methods that nothing *in the library* calls
   (plus any ``main``), ranked by how much code they reach; names like ``main``, ``cli`` and
   ``run``, and anything in ``__main__.py`` / ``cli.py``, come first. Each group keeps its top
   ``MAX_ROOTS`` and is walked three calls deep through its top-level package.

Every view "owns" its group's files (``owns``), which the overview uses for its arrows, and gets
its own colour.

Everything here is a heuristic: if the views are not the ones you want, run ``callgraph spec`` and
edit the printed spec.
"""

import os
import re
from collections import Counter, deque
from typing import Dict, List, Tuple

from .graph import CallGraph
from .index import SOURCE_SUFFIXES, Index
from .symbols import TOPLEVEL

COLORS = ("teal", "indigo", "amber", "red", "purple", "blue", "olive", "rose", "taupe", "slate")
SKIP_DIRS = {"tests", "test", "testing", "docs", "doc", "examples", "example", "benchmarks", "build",
             "dist", "node_modules", "venv", "env", "site-packages", "__pycache__"}
SKIP_FILES = {"setup.py", "conftest.py", "noxfile.py"}
PRIORITY_NAMES = {"main", "cli", "run", "app", "serve", "entrypoint", "__call__"}
PRIORITY_FILES = {"__main__.py", "cli.py", "main.py", "app.py"}
DEPTH, REACH_DEPTH, MAX_NODES = 3, 3, 80
DRIVER_MAX_ROOTS, DRIVER_MAX_NODES = 30, 150


def _has_python(path: str) -> bool:
    if os.path.isfile(path):
        return path.endswith(SOURCE_SUFFIXES)
    for d, dirs, files in os.walk(path):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith(".")]
        if any(f.endswith(SOURCE_SUFFIXES) for f in files):
            return True
    return False


def detect_include(root: str) -> List[str]:
    """Folders and files to parse, relative to ``root``."""
    out = []
    for name in sorted(os.listdir(root)):
        if name.startswith(".") or name in SKIP_DIRS or name in SKIP_FILES:
            continue
        full = os.path.join(root, name)
        if (os.path.isdir(full) or name.endswith(SOURCE_SUFFIXES)) and _has_python(full):
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


def _driver(ix: Index, path: str) -> bool:
    mod = ix.by_path.get(path)
    return bool(mod and mod.driver)


def driver_group(path: str) -> Tuple[str, str]:
    """``(group name, title)`` of a notebook or script: its top folder, or the top level."""
    _, rest = _strip_src(path)
    if "/" not in rest:
        return ".", "top-level scripts"
    top = rest.split("/")[0]
    return top, top


def _owns_files(paths: List[str]) -> str:
    """A regex matching exactly these files (library modules may sit in the same folder)."""
    return "^(" + "|".join(re.escape(p) for p in sorted(paths)) + ")$"


def _choose_level(ix: Index) -> int:
    lib = [f for f in ix.funcs.values() if not _driver(ix, f.path)]
    counts = Counter(group_key(f.path, 1)[0] for f in lib)
    if not counts:
        return 1
    top, n = counts.most_common(1)[0]
    deeper = {group_key(f.path, 2)[0] for f in lib if group_key(f.path, 1)[0] == top}
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
    """A library function nothing in the library calls. Calls from notebooks and scripts do not count,
    nor do calls from a module's top-level code (``if __name__ == "__main__":`` demos, import-time setup)."""
    f = ix.funcs[fid]
    if f.name == TOPLEVEL:  # import-time code of a library module: in the graph, but not a starting point
        return False
    if f.name == "main":
        return True
    if f.name.startswith("_") and f.name != "__call__":
        return False
    if f.cls and Index.is_property(f):
        return False
    return not any(not _driver(ix, e.caller.split("::")[0]) and not e.caller.endswith("::" + TOPLEVEL)
                   for e in graph.callers(fid))


def fill(spec, ix: Index, graph: CallGraph, repo_name: str) -> None:
    """Complete an automatic ``spec`` in place: ``VIEWS``, ``COLOR_RULES``, ``TITLE``, ``SEARCH_EXAMPLE``."""
    spec.TITLE = spec.TITLE or repo_name
    views, rules = [], []

    def add_view(name, title, tag, roots, include, owns, max_nodes):
        color = COLORS[len(views) % len(COLORS)]
        views.append({"id": re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or f"view{len(views)}",
                      "title": title, "tag": tag, "color": f"--c-{color}", "roots": roots, "include": [include],
                      "owns": [owns], "depth": DEPTH, "max_nodes": max_nodes})
        rules.append((owns, color))

    # notebooks and scripts: every one whose code reaches into the repository, in file order
    drivers: Dict[str, Tuple[str, List[Tuple[tuple, str]]]] = {}
    files: Dict[str, List[str]] = {}
    for path, mod in ix.by_path.items():
        if mod.driver:
            files.setdefault(driver_group(path)[0], []).append(path)
    for fid, f in ix.funcs.items():
        if not _driver(ix, f.path) or (f.name != TOPLEVEL and graph.callers(fid)) or _reach(graph, fid) == 0:
            continue
        key, title = driver_group(f.path)
        drivers.setdefault(key, (title, []))[1].append(((f.path, f.lineno), fid))
    for key in sorted(drivers, key=lambda k: (k == ".", k)):
        title, cands = drivers[key]
        owns = _owns_files(files[key])
        roots = [fid for _, fid in sorted(cands)[:DRIVER_MAX_ROOTS]]
        kinds = {"notebook" if r.split("::")[0].endswith(".ipynb") else "script" for r in roots}
        noun = " / ".join(sorted(kinds)) + ("s" if len(roots) != 1 else "")
        add_view("drivers_" + key, title, f"{len(roots)} {noun}", roots, ".", owns, DRIVER_MAX_NODES)

    # the library: per package (or per module of a single package)
    level = _choose_level(ix)
    groups: Dict[str, Tuple[str, List[Tuple[tuple, str]]]] = {}
    for fid, f in ix.funcs.items():
        if _driver(ix, f.path) or not _is_entry_candidate(ix, graph, fid):
            continue
        reach = _reach(graph, fid)
        if reach == 0:
            continue
        name, prefix = group_key(f.path, level)
        prio = int(f.name in PRIORITY_NAMES or os.path.basename(f.path) in PRIORITY_FILES)
        groups.setdefault(name, (prefix, []))[1].append(((-prio, -reach, f.path, f.lineno), fid))

    for name in sorted(groups):
        prefix, cands = groups[name]
        roots = [fid for _, fid in sorted(cands)[: spec.MAX_ROOTS]]
        owns = rf"^{re.escape(prefix)}" + ("" if prefix.endswith("/") else "$")
        _, top = group_key(prefix.rstrip("/"), 1)  # the top-level package this group sits in
        include = rf"^{re.escape(top)}" if top.endswith("/") else r"."  # top-level module: everything
        add_view(name, name, f"{len(roots)} entry point{'s' if len(roots) != 1 else ''}", roots, include, owns, MAX_NODES)
    spec.VIEWS = views
    spec.COLOR_RULES = list(spec.COLOR_RULES) + rules
    names = [ix.funcs[r].name for v in views for r in v["roots"] if not ix.funcs[r].name.startswith(("_", "<"))]
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
