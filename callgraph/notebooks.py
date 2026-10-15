"""
Jupyter notebooks as Python source.

The code cells of a notebook are joined into one Python module so the indexer can treat it like a
``.py`` file. IPython-only syntax is neutralised line by line, keeping line counts so locations
still map back to cells:

- cell magics (``%%time``, ``%%bash``, ...) blank the whole cell;
- line magics (``%matplotlib inline``), shell escapes (``!pip install x``), assignments from them
  (``files = !ls``) and help lookups (``obj?``) become ``pass``.

A cell that still does not parse (a half-finished experiment) is left out on its own; the rest of
the notebook is kept.
"""

import ast
import json
import re
from typing import List, Tuple

_MAGIC = re.compile(r"^(\s*)(?:[\w.\[\]'\", ]+=\s*)?[%!]")  # %magic, !shell, x = !shell, x = %magic
_HELP = re.compile(r"^\s*[\w.]+\?{1,2}\s*$")  # obj? / obj??


def _cell_lines(cell: dict) -> List[str]:
    src = cell.get("source", "")
    text = "".join(src) if isinstance(src, list) else str(src)
    return text.split("\n")


def _neutralise(lines: List[str]) -> List[str]:
    if lines and lines[0].lstrip().startswith("%%"):
        return [""] * len(lines)
    out = []
    for line in lines:
        m = _MAGIC.match(line)
        if m:
            out.append(m.group(1) + "pass")
        elif _HELP.match(line):
            out.append("pass")
        else:
            out.append(line)
    return out


def notebook_source(text: str) -> Tuple[str, List[Tuple[int, int]], int, str]:
    """``(python source, [(first line, cell number)], cells skipped, description)`` of a notebook.

    Cell numbers count every cell, markdown included (1-based), as a reader sees them top to
    bottom. The description is the first paragraph of the first markdown cell, headings stripped.
    """
    nb = json.loads(text)
    lines: List[str] = []
    cells: List[Tuple[int, int]] = []
    skipped, doc = 0, ""
    for number, cell in enumerate(nb.get("cells", []), 1):
        kind = cell.get("cell_type")
        if kind == "markdown" and not doc:
            para = "\n".join(_cell_lines(cell)).strip().split("\n\n")[0]
            doc = re.sub(r"\s+", " ", re.sub(r"^#+\s*", "", para, flags=re.M)).strip()
            continue
        if kind != "code":
            continue
        code = _neutralise(_cell_lines(cell))
        try:
            ast.parse("\n".join(code))
        except SyntaxError:
            skipped += 1
            code = [""] * len(code)
        cells.append((len(lines) + 1, number))
        lines += code
    return "\n".join(lines) + "\n", cells, skipped, doc[:200]


def cell_of(cells: List[Tuple[int, int]], line: int) -> int:
    """The cell number holding ``line`` of the joined source."""
    number = cells[0][1] if cells else 1
    for start, n in cells:
        if start > line:
            break
        number = n
    return number
