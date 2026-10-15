"""
Rendering page data into a single self-contained HTML file.

The page shell (``web/index.html``), stylesheet (``web/app.css``), layout module (``web/layout.js``),
minimap (``web/minimap.js``) and renderer (``web/app.js``) are inlined together with the data, so the output opens straight
from disk or can be published as-is. Only Cytoscape, elkjs and the IBM Plex fonts are loaded from
public CDNs.
"""

import html
import json
import os

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def _read(name: str) -> str:
    with open(os.path.join(WEB_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def data_script(data: dict) -> str:
    """``window.CALLGRAPH = {...};`` with ``</`` escaped so the JSON cannot close the script tag."""
    return "window.CALLGRAPH = " + json.dumps(data, ensure_ascii=False).replace("</", "<\\/") + ";"


def render_html(data: dict, title: str = "Project", search_example: str = "main") -> str:
    """The complete page for ``data`` (see :mod:`callgraph.views.builder` for its schema)."""
    page = _read("index.html")
    page = page.replace("{{TITLE}}", html.escape(title)).replace("{{SEARCH_EXAMPLE}}", html.escape(search_example))
    # Inline last, in this order, so placeholder-looking text inside the data or code is left alone.
    page = page.replace("/*__CSS__*/", _read("app.css"), 1)
    page = page.replace("/*__LAYOUT__*/", _read("layout.js"), 1)
    page = page.replace("/*__MINIMAP__*/", _read("minimap.js"), 1)
    page = page.replace("/*__APP__*/", _read("app.js"), 1)
    head, tail = page.split("/*__DATA__*/", 1)
    return head + data_script(data) + tail


def write_html(path: str, data: dict, title: str, search_example: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_html(data, title, search_example))


def write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
