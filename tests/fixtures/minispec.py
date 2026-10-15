"""Spec for the test fixture repo."""

TITLE = "MiniRepo"
SEARCH_EXAMPLE = "run"
INDEX_INCLUDE = ["main.py", "app"]
SKIP = [r"::log$"]
COLOR_RULES = [(r"^main\.py$", "slate"), (r"^app/model/", "teal"), (r"^app/", "indigo")]
OVERVIEW = False
VIEWS = [
    {"id": "main", "title": "Main", "tag": "main.py", "color": "--c-slate",
     "roots": ["main.py::main", "main.py::does_not_exist"], "include": [r"^main\.py$", r"^app/"], "depth": 4, "max_nodes": 50},
    {"id": "parts", "title": "Parts", "roots": ["app/model/base.py::Base.run"], "include": [r"^app/model/base\.py$"], "depth": 2},
]
