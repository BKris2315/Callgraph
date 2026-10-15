"""
Command-line interface. Run ``callgraph <command> --help`` for details.

    callgraph build                       # HTML page for HEAD of the repo in the current folder
    callgraph build --repo ~/src/project  # ... of another repo
    callgraph trace Session.send -d 2     # call tree below a function
    callgraph trace parse_args --up       # who reaches it
    callgraph edges --from Session.send   # list connections
    callgraph edges --view overview -f tsv
    callgraph find send                   # look up ids
    callgraph views                       # list the page's views
    callgraph spec > myspec.py            # the automatic spec, to edit and pass back with --spec
    callgraph annotate --html page.html   # optional: a local LLM describes functions and views
    callgraph check-layout                # do texts and labels overlap on the rendered page?

Every command analyses one snapshot: ``--rev`` (default HEAD) exported with git archive, or the
working tree with ``--worktree`` (always used for folders that are not git repositories). The
repository is ``--repo``, else ``$CALLGRAPH_REPO``, else the current folder.
"""

import argparse
import contextlib
import json
import os
import re
import sys
import tempfile
from typing import Iterator, List, Optional

from .graph import CallGraph, describe_loop, short_label
from .index import Index
from . import autospec
from .overlay import Overlay
from .render import write_html, write_json
from .resolver import Resolver
from .source import Snapshot, SourceError, snapshot
from .specs import load_spec
from .trace import format_tree, trace
from .views import build_page

class CliError(Exception):
    """A user-facing error: printed without a traceback."""


# ============================================================================ shared plumbing
def _repo(args) -> str:
    return os.path.abspath(os.path.expanduser(args.repo or os.environ.get("CALLGRAPH_REPO") or "."))


@contextlib.contextmanager
def _session(args) -> Iterator[tuple]:
    """Yield ``(spec, snapshot, index, resolver)`` for the snapshot the arguments select."""
    try:
        spec = load_spec(args.spec)
    except FileNotFoundError as e:
        raise CliError(str(e))
    try:
        with snapshot(_repo(args), args.rev, args.worktree) as snap:
            if not spec.INDEX_INCLUDE:
                spec.INDEX_INCLUDE = autospec.detect_include(snap.root)
            if not spec.INDEX_INCLUDE:
                raise CliError(f"no Python code found in {snap.repo}")
            ix = Index(snap.root, spec.INDEX_INCLUDE, spec.INDEX_EXCLUDE)
            for e in ix.parse_errors:
                print(f"  parse error: {e}", file=sys.stderr)
            yield spec, snap, ix, Resolver(ix)
    except SourceError as e:
        raise CliError(str(e))


def _graph(spec, ix: Index, rs: Resolver, keep_noise: bool = False) -> CallGraph:
    return CallGraph(ix, rs, [] if keep_noise else [re.compile(p) for p in spec.SKIP])


def _func(ix: Index, query: str) -> str:
    """User-typed function name -> exactly one id, or a helpful error."""
    hits = ix.find(query)
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise CliError(f"no function or class matches '{query}' (try: callgraph find {query.split('.')[-1]})")
    exact = [h for h in hits if ix.obj(h).qual == query or h == query]
    if len(exact) == 1:
        return exact[0]
    listing = "\n".join(f"  {h}" for h in hits[:25]) + ("\n  …" if len(hits) > 25 else "")
    raise CliError(f"'{query}' is ambiguous; use one of these ids:\n{listing}")


def _loc(ix: Index, oid: str) -> str:
    o = ix.obj(oid)
    return ix.loc(oid)


def _build(args, spec, snap: Snapshot, ix: Index, rs: Resolver):
    overlay_path = None if getattr(args, "no_overlay", False) else (getattr(args, "overlay", None) or spec.OVERLAY)
    return build_page(ix, spec, Overlay.load(overlay_path), snap, rs, repo_name=os.path.basename(snap.repo))


# ============================================================================ commands
def cmd_build(args) -> None:
    with _session(args) as (spec, snap, ix, rs):
        page = _build(args, spec, snap, ix, rs)
    out = args.out or f"callgraph-{snap.sha[:8]}.html"
    write_html(out, page.data, spec.TITLE, spec.SEARCH_EXAMPLE)
    if args.json:
        write_json(os.path.splitext(out)[0] + ".json", page.data)
    print(f"{out}\n  {snap.label} · {len(page.data['views'])} views · {page.node_count} functions · "
          f"{page.edge_count} calls · source: {', '.join(spec.INDEX_INCLUDE)}")
    for w in page.warnings:
        print(f"  warning: {w}")


def cmd_views(args) -> None:
    with _session(args) as (spec, snap, ix, rs):
        page = _build(args, spec, snap, ix, rs)
    rows = [(vid, v["title"], len(v["nodes"]), len(v["edges"])) for vid, v in page.data["views"].items()]
    w = max(len(r[0]) for r in rows)
    print(f"{'view':<{w}}  nodes  edges  title")
    for vid, title, nn, ne in rows:
        print(f"{vid:<{w}}  {nn:>5}  {ne:>5}  {title}")


def cmd_find(args) -> None:
    with _session(args) as (_spec, _snap, ix, _rs):
        hits = ix.find(args.name)
        if not hits:
            raise CliError(f"nothing matches '{args.name}'")
        for h in hits:
            o = ix.obj(h)
            kind = "class" if h in ix.classes else "def"
            print(f"{kind:5} {h}  ({ix.loc(h)})" + (f"  — {o.doc[:80]}" if args.doc and o.doc else ""))


def cmd_trace(args) -> None:
    with _session(args) as (spec, _snap, ix, rs):
        g = _graph(spec, ix, rs, args.keep_noise)
        root = _func(ix, args.function)
        if root in ix.classes:
            init = ix.find_method(root, "__init__")
            if not init:
                raise CliError(f"{root} is a class without __init__; trace one of its methods instead")
            root = init
        tree = trace(g, root, depth=args.depth, up=args.up, include=args.include, exclude=args.exclude)
        if args.json:
            print(json.dumps(tree.to_dict(g), indent=1, ensure_ascii=False))
        else:
            print(format_tree(g, tree, show_args=args.args, show_loc=not args.no_loc))


def _edge_rows_repo(args, ix: Index, g: CallGraph) -> List[dict]:
    src = _func(ix, args.source) if args.source else None
    dst = _func(ix, args.target) if args.target else None
    path_rx = re.compile(args.path) if args.path else None
    if src:
        edges = g.callees(src, unique=not args.all_sites)
    elif dst:
        edges = g.callers(dst, unique=not args.all_sites)
    else:
        edges = list(g.edges())
    rows = []
    for e in edges:
        if dst and e.callee != dst:
            continue
        if path_rx and not (path_rx.search(ix.obj(e.caller).path) or path_rx.search(ix.obj(e.callee).path)):
            continue
        rows.append({"caller": e.caller, "callee": e.callee, "caller_label": short_label(ix, e.caller),
                     "callee_label": short_label(ix, e.callee), "where": ix.loc(e.caller, e.line),
                     "order": None, "data": describe_loop(e.loop) + f"({e.args})", "condition": e.condition,
                     "inferred": e.inferred})
    if src:
        for i, r in enumerate(rows, 1):
            r["order"] = i
    return rows


def _edge_rows_view(page_data: dict, vid: str) -> List[dict]:
    v = page_data["views"].get(vid)
    if not v:
        raise CliError(f"no view '{vid}'; available: {', '.join(page_data['views'])}")
    nodes = {n["id"]: n for n in v["nodes"]}
    rows = []
    for s, t, order, data, cond in v["edges"]:
        rows.append({"caller": s, "callee": t, "caller_label": nodes[s]["label"], "callee_label": nodes[t]["label"],
                     "where": nodes[s].get("loc", ""), "order": order if order != "" else None, "data": data,
                     "condition": cond, "inferred": "[target inferred" in (data or "")})
    return rows


def cmd_edges(args) -> None:
    with _session(args) as (spec, snap, ix, rs):
        if args.view:
            rows = _edge_rows_view(_build(args, spec, snap, ix, rs).data, args.view)
        else:
            rows = _edge_rows_repo(args, ix, _graph(spec, ix, rs, args.keep_noise))
    if args.format == "json":
        print(json.dumps(rows, indent=1, ensure_ascii=False))
    elif args.format == "tsv":
        cols = ["caller", "callee", "order", "where", "data", "condition", "inferred"]
        print("\t".join(cols))
        for r in rows:
            print("\t".join("" if r[c] is None else str(r[c]).replace("\t", " ") for c in cols))
    else:
        for r in rows:
            num = f"{r['order']:>3} " if r["order"] is not None else "    "
            cond = f"  [if {r['condition']}]" if r["condition"] else ""
            print(f"{num}{r['caller_label']} → {r['callee_label']}  {r['where']}  {r['data']}{cond}")
        sys.stdout.flush()
        print(f"\n{len(rows)} connections", file=sys.stderr)


def cmd_spec(args) -> None:
    with _session(args) as (spec, snap, ix, rs):
        if spec.AUTO and not spec.VIEWS:
            autospec.fill(spec, ix, CallGraph(ix, rs), os.path.basename(snap.repo))
    sys.stdout.write(autospec.render_spec(spec))


def cmd_annotate(args) -> None:
    from .annotate import TransformersBackend, annotate

    existing = {}
    if os.path.isfile(args.out) and os.path.getsize(args.out):
        try:
            with open(args.out, encoding="utf-8") as fh:
                existing = json.load(fh)
        except ValueError as e:
            raise CliError(f"{args.out} exists but is not an overlay JSON file ({e}); pick another -o")
    with _session(args) as (spec, snap, ix, rs):
        page = build_page(ix, spec, Overlay(), snap, rs, repo_name=os.path.basename(snap.repo))
        graph = _graph(spec, ix, rs)
        print(f"loading {args.model} …", file=sys.stderr)
        try:
            backend = TransformersBackend(args.model, device=args.device, dtype=args.dtype,
                                          max_new_tokens=args.max_new_tokens, batch_size=args.batch_size)
        except RuntimeError as e:
            raise CliError(str(e))
        overlay, st = annotate(ix, graph, page.data, backend, existing, commit=snap.sha, only_missing=not args.all,
                               views=not args.no_views, refresh=args.refresh, limit=args.limit,
                               progress=lambda m: print(m, file=sys.stderr))
        write_json(args.out, overlay)
        print(f"{args.out}: {st.generated} functions described, {st.kept} unchanged (cached), {st.human} hand-written kept, "
              f"{st.skipped_documented} with docstrings skipped, {st.views} view intros · {st.seconds}s")
        if args.html:
            final = build_page(ix, spec, Overlay(overlay), snap, rs, repo_name=os.path.basename(snap.repo))
            write_html(args.html, final.data, spec.TITLE, spec.SEARCH_EXAMPLE)
            print(args.html)


def cmd_check_layout(args) -> int:
    from .layout_check import DIRECTIONS, CheckUnavailable, check_page, format_report

    try:
        width, height = (int(x) for x in args.viewport.lower().split("x"))
    except ValueError:
        raise CliError(f"--viewport must look like 1600x1000, not '{args.viewport}'")
    directions = DIRECTIONS if args.direction == "all" else (args.direction.upper(),)
    with tempfile.TemporaryDirectory(prefix="callgraph-check-") as tmp:
        page = args.page
        if not page:  # build one from the source options first
            with _session(args) as (spec, snap, ix, rs):
                built = _build(args, spec, snap, ix, rs)
            page = os.path.join(tmp, "page.html")
            write_html(page, built.data, spec.TITLE, spec.SEARCH_EXAMPLE)
        elif not os.path.isfile(page):
            raise CliError(f"page not found: {page}")
        try:
            results = check_page(page, views=args.view or None, directions=directions, viewport=(width, height),
                                 tolerance=args.tolerance, dark=args.dark, screenshots_dir=args.screenshots,
                                 chromium=args.chromium)
        except (CheckUnavailable, ValueError) as e:
            raise CliError(str(e))
    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=1, ensure_ascii=False))
    else:
        print(format_report(results, show_warnings=args.warnings, limit=args.limit))
    failed = any(r.errors for r in results) or (args.strict and any(r.warnings for r in results))
    return 1 if failed else 0


# ============================================================================ argument parsing
def _source_args(p: argparse.ArgumentParser, worktree: bool = True) -> None:
    g = p.add_argument_group("source")
    g.add_argument("--repo", help="repository root (default: $CALLGRAPH_REPO, else the current folder)")
    g.add_argument("-r", "--rev", default="HEAD", help="commit, branch or tag to analyse (default HEAD)")
    if worktree:
        g.add_argument("-w", "--worktree", action="store_true", help="analyse the working tree as it is, uncommitted edits included")
    g.add_argument("--spec", default="auto",
                   help="view spec: 'auto' (default: views chosen from the code) or a .py spec file (see: callgraph spec)")


def _overlay_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--overlay", help="JSON with hand-written descriptions (default: the spec's OVERLAY, if any)")
    p.add_argument("--no-overlay", action="store_true", help="docstrings only, ignore the overlay")


def make_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="callgraph", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True, metavar="command")

    p = sub.add_parser("build", help="write the interactive HTML page")
    _source_args(p)
    _overlay_args(p)
    p.add_argument("-o", "--out", help="output .html (default: ./callgraph-<sha8>.html)")
    p.add_argument("--json", action="store_true", help="also write the page data as .json next to the .html")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("trace", help="print the call tree below (or above, with --up) a function")
    p.add_argument("function", help="id (path::Qual.name), Qual.name (Model.forward) or a unique bare name")
    p.add_argument("-d", "--depth", type=int, default=3, help="levels to follow (default 3)")
    p.add_argument("--up", action="store_true", help="follow callers instead of callees")
    p.add_argument("-i", "--include", action="append", default=[], metavar="REGEX",
                   help="only expand functions whose file matches (repeatable); others are shown, not expanded")
    p.add_argument("-x", "--exclude", action="append", default=[], metavar="REGEX", help="leave out ids matching (repeatable)")
    p.add_argument("-a", "--args", action="store_true", help="show the arguments of each call")
    p.add_argument("--no-loc", action="store_true", help="hide file:line")
    p.add_argument("--keep-noise", action="store_true", help="keep the callees the spec's SKIP list hides")
    p.add_argument("--json", action="store_true", help="print the tree as JSON")
    _source_args(p)
    p.set_defaults(func=cmd_trace)

    p = sub.add_parser("edges", help="list connections (caller → callee) in the repo or in one page view")
    sel = p.add_mutually_exclusive_group()
    sel.add_argument("--view", help="list the edges of this page view exactly as drawn (see: callgraph views)")
    sel.add_argument("--from", dest="source", metavar="FUNC", help="calls made by FUNC, in source order")
    p.add_argument("--to", dest="target", metavar="FUNC", help="calls into FUNC")
    p.add_argument("--path", metavar="REGEX", help="keep edges whose caller or callee file matches")
    p.add_argument("--all-sites", action="store_true", help="one row per call site instead of per caller/callee pair")
    p.add_argument("--keep-noise", action="store_true", help="keep the callees the spec's SKIP list hides")
    p.add_argument("-f", "--format", choices=("text", "tsv", "json"), default="text")
    _source_args(p)
    _overlay_args(p)
    p.set_defaults(func=cmd_edges)

    p = sub.add_parser("find", help="look up functions and classes by name")
    p.add_argument("name")
    p.add_argument("--doc", action="store_true", help="show the first line of each docstring")
    _source_args(p)
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("views", help="list the views the page would contain")
    _source_args(p)
    _overlay_args(p)
    p.set_defaults(func=cmd_views)

    p = sub.add_parser("spec", help="print the automatic spec as a file to edit and pass back with --spec")
    _source_args(p)
    p.set_defaults(func=cmd_spec)

    p = sub.add_parser("annotate", help="optional: let a local LLM (Hugging Face transformers) describe functions and views",
                       description="Describe the page's functions and views with a language model running locally, and "
                                   "write them as an overlay file for `callgraph build --overlay`. Greedy decoding and a "
                                   "cache keyed on each function's code keep the text stable between runs; entries "
                                   "written by people are never replaced. Needs: pip install -e '.[llm]'")
    from .annotate import DEFAULT_MODEL
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"Hugging Face model id or local folder (default {DEFAULT_MODEL})")
    p.add_argument("-o", "--out", default="callgraph-notes.json", help="overlay file to write; an existing one is reused as cache (default callgraph-notes.json)")
    p.add_argument("--html", metavar="PATH", help="also build the page with the new descriptions")
    p.add_argument("--all", action="store_true", help="describe every function, not only those without a docstring")
    p.add_argument("--no-views", action="store_true", help="do not write view introductions")
    p.add_argument("--refresh", action="store_true", help="ignore cached model text and regenerate it")
    p.add_argument("--limit", type=int, help="describe at most this many functions (for a quick try)")
    p.add_argument("--device", default="auto", help="auto (all GPUs, else CPU), cpu, cuda, cuda:1 (default auto)")
    p.add_argument("--dtype", default="auto", choices=("auto", "bfloat16", "float16", "float32"))
    p.add_argument("--batch-size", type=int, default=1,
                   help="prompts per forward pass; 1 (default) gives reproducible text, more is faster but can change wording")
    p.add_argument("--max-new-tokens", type=int, default=160)
    _source_args(p)
    p.set_defaults(func=cmd_annotate)

    p = sub.add_parser("check-layout", help="render the page in headless Chromium and report overlapping texts and labels",
                       description="Render every view in headless Chromium and report text that collides: function text "
                                   "outside its box, boxes on top of each other, group titles on functions or other "
                                   "titles, call numbers on text (errors), and edge lines across text (warnings). "
                                   "Exit status 1 when there are errors. Needs: pip install playwright && playwright install chromium")
    p.add_argument("page", nargs="?", help="a page from `callgraph build`; built from the source options when omitted")
    p.add_argument("--view", action="append", default=[], help="only this view (repeatable); default all")
    p.add_argument("--direction", choices=("auto", "down", "right", "all"), default="auto",
                   help="layout direction to check; 'all' checks auto, down and right (default auto)")
    p.add_argument("--viewport", default="1600x1000", help="browser size; the layout adapts to it (default 1600x1000)")
    p.add_argument("--tolerance", type=float, default=1.0, help="ignore overlaps up to this many px (default 1)")
    p.add_argument("--dark", action="store_true", help="render with the dark colour scheme")
    p.add_argument("--warnings", action="store_true", help="also list warnings (edges across text)")
    p.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    p.add_argument("--screenshots", metavar="DIR", help="save a screenshot of each view with issues, colliding areas outlined")
    p.add_argument("--limit", type=int, default=12, help="issues listed per view (default 12)")
    p.add_argument("--json", action="store_true", help="print the results as JSON")
    p.add_argument("--chromium", metavar="PATH", help="Chromium/Chrome executable to use instead of Playwright's")
    _source_args(p)
    _overlay_args(p)
    p.set_defaults(func=cmd_check_layout)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        rc = args.func(args) or 0
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except CliError as e:
        print(f"callgraph: {e}", file=sys.stderr)
        return 2
    try:
        sys.stdout.flush()
    except BrokenPipeError:  # output piped into e.g. `head`, which exited early
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return rc


if __name__ == "__main__":
    sys.exit(main())
