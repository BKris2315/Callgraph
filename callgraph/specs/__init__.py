"""
View specs: what the generated page contains.

The default spec is ``auto``: it finds the source folders, the packages and their entry points by
itself (see :mod:`callgraph.autospec`). For full control, write a spec file, a plain Python
module of constants, and pass it with ``--spec path/to/myspec.py``. ``callgraph spec`` prints
the automatic spec of a repository as a file to start from.

:func:`load_spec` fills in defaults for anything a spec leaves out, so a minimal spec only needs
``INDEX_INCLUDE`` and ``VIEWS``.

Spec attributes
---------------
TITLE           str   project name shown in the page header and ``<title>`` ("" = repository folder name)
SEARCH_EXAMPLE  str   example function name in the search placeholder
OVERLAY         str   optional JSON with hand-written descriptions, relative to the spec file
INDEX_INCLUDE   list  files/folders to parse, relative to the repo root (empty = detect)
INDEX_EXCLUDE   list  path regexes never parsed
SKIP            list  id regexes of callees never drawn (noise such as logging helpers)
COLOR_RULES     list  ``(path regex, colour)``; colours: slate amber indigo teal red purple olive
                      taupe blue rose. First match wins.
VIEWS           list  views walked from fixed root functions (see :mod:`callgraph.views.pipeline`);
                      empty = choose automatically
OVERVIEW        bool  add an overview with one box per view and the calls between views
MAX_ROOTS       int   automatic views: at most this many entry points per view
"""

import importlib.util
import os
from types import SimpleNamespace

DEFAULT_EXCLUDE = [
    r"(^|/)tests?/", r"(^|/)testing/", r"(^|/)docs?/", r"(^|/)examples?/", r"(^|/)benchmarks?/",
    r"(^|/)build/", r"(^|/)dist/", r"(^|/)node_modules/", r"(^|/)site-packages/", r"(^|/)\.",
    r"(^|/)(setup|conftest|noxfile|test_[^/]*|[^/]*_test)\.py$",
]

DEFAULTS = {
    "TITLE": "",
    "SEARCH_EXAMPLE": "main",
    "OVERLAY": "",
    "INDEX_INCLUDE": [],
    "INDEX_EXCLUDE": DEFAULT_EXCLUDE,
    "SKIP": [],
    "COLOR_RULES": [],
    "VIEWS": [],
    "OVERVIEW": True,
    "MAX_ROOTS": 6,
}


def load_spec(name_or_path: str = "auto") -> SimpleNamespace:
    """Load ``"auto"`` or a spec file, with :data:`DEFAULTS` for missing attributes.

    The result also carries ``NAME``, ``PATH`` (the spec file, "" for auto) and ``AUTO`` (whether
    views are chosen automatically: the auto spec, or a file without ``VIEWS``). ``OVERLAY`` is
    turned into an absolute path or "".
    """
    if name_or_path in ("", "auto"):
        values = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}
        values.update(NAME="auto", PATH="", AUTO=True)
        return SimpleNamespace(**values)
    path = os.path.abspath(os.path.expanduser(name_or_path))
    if not os.path.isfile(path):
        raise FileNotFoundError(f"spec file not found: {path} (or use --spec auto)")
    modspec = importlib.util.spec_from_file_location(f"callgraph_spec_{os.path.basename(path)[:-3]}", path)
    mod = importlib.util.module_from_spec(modspec)
    modspec.loader.exec_module(mod)
    values = {k: getattr(mod, k, v) for k, v in DEFAULTS.items()}
    values.update(NAME=name_or_path, PATH=path, AUTO=not values["VIEWS"])
    if values["OVERLAY"]:
        values["OVERLAY"] = os.path.join(os.path.dirname(path), values["OVERLAY"])
    return SimpleNamespace(**values)
