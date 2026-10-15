"""
Optional hand-written descriptions layered on top of the generated graph.

The generator only knows docstrings and call arguments. An overlay file adds your own text:
function descriptions, view intros and notes. Pass it with ``--overlay file.json`` or set
``OVERLAY`` in a spec.

Overlay file format (JSON)::

    {
      "commit": "<sha the text was written against>",          # optional
      "views":  {"<view id>": {"title": ..., "subtitle": ..., "intro": [...], "notes": [...], "commit": ...}},
      "nodes":  {"pkg/mod.py::Class.method": {"desc": ..., "note": ..., "commit": ..., "body_hash": ...}},
      "overview_nodes": {"o_<view id>": "description of the overview box"}
    }

Entries with a ``"model"`` field were written by a language model (``callgraph annotate``); the page
says so next to them. A function description is shown only while that function still exists. When its ``body_hash``
(see ``Func.body_hash``) no longer matches, the page adds a note that the text may be out of date.
"""

import json
import os
from typing import Optional, Tuple

MODEL_NOTE = "Description written by the language model {model}, not by a person; check it against the code."
STALE_NOTE = ("The description above was written at commit {commit}; this function has changed since, "
              "so it may be out of date.")


class Overlay:
    """Read access to an overlay file. An empty overlay (no file) answers every lookup with nothing."""

    def __init__(self, data: Optional[dict] = None) -> None:
        self.data = data or {}

    @classmethod
    def load(cls, path: Optional[str]) -> "Overlay":
        if not path or not os.path.exists(path):
            return cls()
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    def describe(self, oid: str, obj) -> Tuple[str, Optional[str]]:
        """``(description, note)`` for a function or class: overlay text if any, else its docstring."""
        cur = self.data.get("nodes", {}).get(oid)
        desc, note = obj.doc or "", None
        if cur and cur.get("desc"):
            desc = cur["desc"]
            body_hash = getattr(obj, "body_hash", None)
            if cur.get("body_hash") and body_hash and cur["body_hash"] != body_hash:
                note = STALE_NOTE.format(commit=cur.get("commit", "?")[:8])
        if cur and cur.get("note"):
            note = cur["note"] + (" " + note if note else "")
        if cur and cur.get("desc") and cur.get("model"):
            note = MODEL_NOTE.format(model=cur["model"]) + (" " + note if note else "")
        return desc, note

    def view(self, vid: str) -> dict:
        """Overlay title / subtitle / intro / notes for a view (empty dict if none)."""
        return self.data.get("views", {}).get(vid, {})

    def overview_text(self, box_id: str) -> str:
        return self.data.get("overview_nodes", {}).get(box_id, "")
