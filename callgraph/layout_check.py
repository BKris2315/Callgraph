"""
Rendered-layout check: do texts and labels on the page overlap?

Opens a generated page in headless Chromium (Playwright), lays out every view the way a viewer
would see it, and runs ``web/check_overlaps.js`` on the drawing. That script measures what
Cytoscape actually draws (function boxes, their text, group boxes and titles, call-number labels,
edge lines) and reports each collision; see its header for the list of checks.

Needs the optional dependency ``playwright`` and a Chromium for it::

    pip install -e '.[check]' && playwright install chromium

Layout depends on the canvas size (auto direction, component packing), so the check runs at a
fixed viewport, by default 1600×1000.
"""

import os
from dataclasses import asdict, dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

from .render import WEB_DIR

DIRECTIONS = ("AUTO", "DOWN", "RIGHT")


@dataclass
class Issue:
    kind: str  # e.g. "title-overlap"; see web/check_overlaps.js
    severity: str  # "error" or "warning"
    a: str  # element ids involved (node, group or edge)
    b: str
    a_name: str
    b_name: str
    overlap_w: float  # size of the overlapping area in px (model coordinates), 0 where not meaningful
    overlap_h: float


@dataclass
class ViewResult:
    view: str
    direction: str  # what was requested
    chosen: str  # what the layout used (AUTO resolves to DOWN or RIGHT)
    nodes: int
    issues: List[Issue] = field(default_factory=list)
    screenshot: Optional[str] = None

    @property
    def errors(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["errors"], d["warnings"] = len(self.errors), len(self.warnings)
        return d


class CheckUnavailable(RuntimeError):
    """Playwright or its Chromium is missing."""


_RUN_CHECK = """([tolerance]) => {
  const cy = CG.cy();
  const issues = CGCheck.run(cy, { tolerance });
  const name = (id) => {
    const el = cy.getElementById(id);
    if (el.empty()) return id;
    if (el.isEdge()) return `${el.source().data('label')} → ${el.target().data('label')}` + (el.data('order') !== '' ? ` #${el.data('order')}` : '');
    return String(el.data('label') || id).replace(/\\n/g, ' ');
  };
  for (const i of issues) { i.aName = name(i.a); i.bName = name(i.b); }
  return { issues, labels: cy.nodes('.olabel:visible').length, nodes: cy.nodes(':visible').filter((n) => !n.isParent() && !n.hasClass('olabel') && !n.hasClass('otitle')).length };
}"""

# Draw the colliding areas on top of the fitted graph, for screenshots.
_MARK = """([boxes]) => {
  const cy = CG.cy();
  cy.fit(cy.elements(':visible'), 30);
  const stage = document.getElementById('cy').parentElement;
  const layer = document.createElement('div');
  layer.id = '__cgcheck';
  layer.style.cssText = 'position:absolute;inset:0;pointer-events:none;z-index:50';
  const z = cy.zoom(), pan = cy.pan(), off = document.getElementById('cy').offsetTop;
  for (const [b, sev] of boxes) {
    const d = document.createElement('div');
    const pad = 3;
    d.style.cssText = `position:absolute;left:${b.x1 * z + pan.x - pad}px;top:${b.y1 * z + pan.y + off - pad}px;` +
      `width:${(b.x2 - b.x1) * z + 2 * pad}px;height:${(b.y2 - b.y1) * z + 2 * pad}px;` +
      `border:2px solid ${sev === 'error' ? '#e11d48' : '#f59e0b'};border-radius:3px;background:${sev === 'error' ? 'rgba(225,29,72,.12)' : 'transparent'}`;
    layer.appendChild(d);
  }
  stage.appendChild(layer);
}"""


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise CheckUnavailable("the layout check needs Playwright: pip install playwright && playwright install chromium") from e
    return sync_playwright


def check_page(html_path: str, views: Optional[Sequence[str]] = None, directions: Iterable[str] = ("AUTO",),
               viewport: Tuple[int, int] = (1600, 1000), tolerance: float = 1.0, dark: bool = False,
               screenshots_dir: Optional[str] = None, chromium: Optional[str] = None,
               timeout_ms: int = 30000) -> List[ViewResult]:
    """Lay out ``views`` (default: all) of a generated page in each direction and collect overlaps.

    Args:
        html_path: a page written by ``callgraph build``.
        views: view ids to check; all views when omitted.
        directions: any of "AUTO", "DOWN", "RIGHT".
        viewport: browser window size; the layout adapts to it.
        tolerance: overlaps up to this many px are ignored.
        dark: render with the dark colour scheme.
        screenshots_dir: if set, save a fitted screenshot of each view that has issues, with the
            colliding areas outlined (red: error, amber: warning).
        chromium: path of a Chromium/Chrome executable instead of Playwright's own.
    """
    sync_playwright = _playwright()
    url = "file://" + os.path.abspath(html_path)
    with open(os.path.join(WEB_DIR, "check_overlaps.js"), encoding="utf-8") as fh:
        check_js = fh.read()
    results: List[ViewResult] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=chromium) if chromium else p.chromium.launch()
        except Exception as e:  # missing browser binary or system libraries
            raise CheckUnavailable(f"could not start Chromium: {str(e).splitlines()[0]}\n"
                                   "Run `playwright install chromium` (and `playwright install-deps` for system libraries), "
                                   "or pass --chromium PATH.") from e
        try:
            for direction in directions:
                ctx = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]},
                                          color_scheme="dark" if dark else "light")
                # A clean viewer: no remembered view, collapsed groups or focus; the requested direction.
                ctx.add_init_script(f"try {{ localStorage.clear(); localStorage.setItem('cg:dir', JSON.stringify({direction!r})); }} catch (e) {{}}")
                page = ctx.new_page()
                errors: List[str] = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(url)
                page.wait_for_function("window.CG && document.getElementById('cy').dataset.layout === 'done'", timeout=timeout_ms)
                page.add_script_tag(content=check_js)
                for vid in views or page.evaluate("CG.views()"):
                    if vid not in page.evaluate("CG.views()"):
                        raise ValueError(f"no view '{vid}' in {html_path}")
                    page.evaluate("v => CG.show(v)", vid)
                    page.wait_for_function("document.getElementById('cy').dataset.layout === 'done'", timeout=timeout_ms)
                    chosen = page.evaluate("document.getElementById('cy').dataset.direction || ''") or direction
                    raw = page.evaluate(_RUN_CHECK, [tolerance])
                    res = ViewResult(vid, direction, chosen, raw["nodes"], [
                        Issue(i["kind"], i["severity"], i["a"], i["b"], i["aName"], i["bName"], i["overlap"]["w"], i["overlap"]["h"])
                        for i in raw["issues"]])
                    if screenshots_dir and res.issues:
                        os.makedirs(screenshots_dir, exist_ok=True)
                        page.evaluate(_MARK, [[[i["box"], i["severity"]] for i in raw["issues"]]])
                        res.screenshot = os.path.join(screenshots_dir, f"{vid}-{direction.lower()}.png")
                        page.locator("main.stage").screenshot(path=res.screenshot)
                        page.evaluate("document.getElementById('__cgcheck').remove()")
                    results.append(res)
                if errors:
                    raise RuntimeError("the page raised JavaScript errors:\n  " + "\n  ".join(errors))
                ctx.close()
        finally:
            browser.close()
    return results


def format_report(results: List[ViewResult], show_warnings: bool = False, limit: int = 12) -> str:
    """Human-readable summary: one line per view, then the issues (errors, and warnings if asked)."""
    lines = []
    w = max([len(r.view) for r in results] + [4])
    lines.append(f"{'view':<{w}}  direction     nodes  errors  warnings")
    for r in results:
        d = r.direction.lower() + (f"→{r.chosen.lower()}" if r.direction == "AUTO" and r.chosen in ("DOWN", "RIGHT") else "")
        lines.append(f"{r.view:<{w}}  {d:<12}  {r.nodes:>5}  {len(r.errors):>6}  {len(r.warnings):>8}")
    for r in results:
        shown = r.errors + (r.warnings if show_warnings else [])
        if not shown:
            continue
        lines.append(f"\n{r.view} ({r.direction.lower()}):" + (f"  screenshot: {r.screenshot}" if r.screenshot else ""))
        for i in shown[:limit]:
            size = f"  ({i.overlap_w:g}×{i.overlap_h:g}px)" if i.overlap_w and i.overlap_h else ""
            other = "" if i.a == i.b else f"  ↔  {i.b_name}"
            lines.append(f"  {i.severity:<7} {i.kind:<20} {i.a_name}{other}{size}")
        if len(shown) > limit:
            lines.append(f"  … {len(shown) - limit} more")
    total_e = sum(len(r.errors) for r in results)
    total_w = sum(len(r.warnings) for r in results)
    lines.append(f"\n{total_e} errors, {total_w} warnings in {len(results)} view layouts")
    return "\n".join(lines)
