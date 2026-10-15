# Callgraph

Static call graphs of any Python repository, rendered as an interactive HTML page.

Point it at a repository and you get one self-contained `.html` file. It has an overview of the
code's main areas, and one zoomable graph per area: who calls what, in which order, under which
`if`, and with which arguments. Nothing from the repository is imported or run, so you don't need
its dependencies, and a run takes well under a second.

```bash
pip install -e .                                    # or: pip install -r requirements.txt
callgraph build --repo path/to/any/python/repo      # -> ./callgraph-<sha8>.html
```

No configuration is needed. The views are picked automatically from the code's entry points.
The optional features (local-LLM notes, the layout check, the tests) have their dependencies listed
in [requirements.txt](requirements.txt) as commented blocks, and as pip extras (`.[llm]`,
`.[check]`, `.[dev]`).

## What you get

- **`build`**: the interactive page.
  - A rail of views on the left, the graph in the middle, and an inspector on the right showing
    each function's file:line, docstring, callees (in call order) and callers.
  - Search across all views, collapsible groups, light and dark themes, and a minimap for big
    graphs.
  - *Focus on selection*: click a function to narrow the graph to its callers and callees.
- **`trace`**: the call tree below a function (`--up`: above it), in the terminal.
- **`edges`**: connections (caller → callee, call order, arguments, condition) as text, TSV or JSON.
- **`find`** / **`views`**: look up function ids, or list the page's views.
- **`spec`**: print the automatic configuration as an editable file.
- **`annotate`** (optional): a language model running on your machine writes descriptions for
  functions and views. See below.
- **`check-layout`**: render the page in headless Chromium and report any overlapping text.

```bash
callgraph build                                  # the repo in the current folder, at HEAD
callgraph build -w                               # working tree, uncommitted edits included
callgraph build -r v2.0 -o out/v2.html --json    # any commit, branch or tag; also dump the data

callgraph trace Session.send -d 2                # what it calls, 2 levels deep, in order
callgraph trace parse_args --up                  # what reaches it
callgraph trace Engine.run -a --json             # with call arguments, as JSON

callgraph edges --from Session.send              # its calls, in source order
callgraph edges --to merge_setting -f tsv        # its callers
callgraph edges --path '^src/pkg/io/' -f json    # everything touching a folder
callgraph edges --view overview                  # a page view, exactly as drawn

callgraph find send                              # full ids of everything called "send"
```

Functions can be named by full id (`src/pkg/mod.py::Class.method`), dotted path
(`pkg.mod.Class.method`), qualified name (`Class.method`) or bare name. An ambiguous name lists
the candidates.

The repository is `--repo`, else `$CALLGRAPH_REPO`, else the current folder. In a git repository,
a commit is exported with `git archive` into a temporary folder (submodules at the commits it
records), so your checkout is never touched. A folder that isn't a git repository is read as it is.

Requirements: Python ≥ 3.9 and git. There are no runtime dependencies. The page loads Cytoscape,
elkjs and the IBM Plex fonts from public CDNs when it is opened.

## How the automatic views are chosen

1. **Source**: every top-level folder and file with Python in it (`.py` or Jupyter `.ipynb`),
   `src/` included, except tests, docs, examples, build output, virtual environments and hidden
   folders.
2. **Drivers and library**: notebooks, and scripts that are neither inside a package nor imported
   by other files, *drive* the code. Everything else is the library.
3. **Driver views** come first: one per folder of notebooks or scripts (`notebooks/`, `scripts/`,
   the files at the top level). Every notebook or script whose code reaches into the repository
   is an entry point, in file order.
4. **Library groups**: one per top-level package. When nearly everything is in one package, there
   is one group per sub-package or module instead, so a single-package library still gets several
   views.
5. **Library entry points**: public functions and methods that nothing *in the library* calls
   (plus any `main`), ranked by how much code they reach. Calls from notebooks, scripts and
   `if __name__ == "__main__":` demos don't count against them. Names such as `main`, `cli`,
   `run`, and anything in `__main__.py`, `cli.py`, `main.py` or `app.py`, come first. Each group
   keeps its top 6.
6. **Views**: each view follows its entry points three calls deep, with one box per source file.
7. **Overview**: one box per view, with an arrow wherever one view's code calls into another's.
   When there are notebooks or scripts, it also lists the library files none of them reaches.

### Research repositories, notebooks and scripts

Callgraph is also meant for code that isn't a tidy package: a loose `src/` folder, notebooks that
use it, and scripts written as plain top-level code.

- **Notebooks** are read cell by cell. `%magics`, `!shell` lines and `obj?` help lookups are
  neutralised. A cell that isn't valid Python is skipped (and reported), and the rest of the
  notebook is kept. Locations read `analysis.ipynb:cell 7`, and the first markdown cell becomes
  the notebook's description.
- **Top-level code** of every file is analysed as if it were a function named after the file:
  script bodies, notebook cells, `if __name__ == "__main__":` blocks. `while` and `for` loops and
  `if` conditions around each call are kept.
- **`sys.path` imports.** Notebooks and scripts that reach the library through `sys.path` (e.g.
  `import data` for `src/lab/data.py`) are resolved when the name matches exactly one module. This
  applies only to notebooks and scripts, and never to standard-library names.
- **Finding them:** `callgraph trace analysis.ipynb` and `callgraph trace train.py` follow a
  notebook or script by file name.

The overview then answers questions such as "which notebooks use which modules" and "which library
files does nothing use any more". Code that is run with `exec(...)` or `%run`, or loaded by
string, is still invisible.

These are heuristics. To choose the views yourself, write the automatic spec to a file, edit it,
and pass it back:

```bash
callgraph spec > myspec.py
callgraph build --spec myspec.py
```

A spec is a plain Python file of constants:

```python
TITLE = "My project"
INDEX_INCLUDE = ["src"]                       # what to parse
SKIP = [r"::log$", r"::debug$"]               # callees never drawn
COLOR_RULES = [(r"^src/pkg/core/", "teal"), (r"^src/pkg/io/", "amber")]
VIEWS = [
    {"id": "core", "title": "Core engine", "roots": ["src/pkg/core/engine.py::Engine.run"],
     "include": [r"^src/pkg/"],               # files the walk may enter; calls leaving them become dashed boxes
     "depth": 3, "max_nodes": 100},
]
```

The full list of settings is in [specs/\_\_init\_\_.py](callgraph/specs/__init__.py). Available
colours: slate, amber, indigo, teal, red, purple, olive, taupe, blue, rose.

### Hand-written descriptions (optional)

By default, nodes show their docstrings. To add your own text (function descriptions, view intros,
notes), pass a JSON overlay with `--overlay notes.json`, or set `OVERLAY` in a spec. The format is
described in [overlay.py](callgraph/overlay.py). A description stays attached to its function
for as long as that function exists, and the page warns when the function has changed since the
text was written.

## Optional: descriptions written by a local language model

`callgraph annotate` asks a model running locally to describe each function and view, and writes
the result as an overlay file (see above). Any Hugging Face `transformers` chat model works: Qwen,
Gemma, Mistral, Llama, … Nothing leaves your machine, and the rest of the tool doesn't need any of
this.

```bash
pip install -e '.[llm]'                         # transformers, torch, accelerate
callgraph annotate --html page.html             # describe, write callgraph-notes.json, build the page
callgraph build --overlay callgraph-notes.json  # later builds reuse the notes

callgraph annotate --model google/gemma-3-4b-it --all       # another model; also documented functions
callgraph annotate --limit 5 --no-views                     # a quick try
callgraph annotate --device cuda:1 --dtype float16          # one GPU, fp16
```

What the model gets for each function:

- its source (up to 80 lines);
- its docstring and its class's docstring;
- what it calls, and what calls it.

It is asked for one or two plain sentences. Each view intro is written from that view's entry
points and the function descriptions.

The default model is `Qwen/Qwen3-4B-Instruct-2507`: open, not gated, about 8 GB in bf16, so it fits
on one 12 GB GPU. It also runs on CPU, slowly. By default only functions without a docstring are
described; `--all` describes every function, and the model's text then replaces the docstring on
the page. Gated models (Gemma, some Mistral or Llama releases) need their licence accepted on
huggingface.co and `huggingface-cli login`.

The output stays stable from run to run:

- **Greedy decoding with fixed prompts, one prompt at a time.** The same model, code and hardware
  give the same text. `--batch-size 8` is faster, but then the wording can depend on which prompts
  share a batch, because padding changes the floating-point arithmetic slightly.
- **A cache keyed on code.** Every entry stores the function's body hash, the model and the prompt
  version. Running `annotate` again keeps entries whose function hasn't changed and regenerates
  only the rest, so text doesn't drift between runs or releases. `--refresh` regenerates everything.
- **People win.** Overlay entries without a `"model"` field were written by a person and are never
  replaced, so you can correct a model's description by editing it and deleting its `"model"` key.
- **The page says who wrote what.** A model-written description or view intro is shown with a note
  naming the model.

If loading fails with an `ImportError` from some other package (for example `torchao` not matching
the installed `torch`), your Python environment has a broken optional package. Use a clean
virtualenv, or set `PYTHONNOUSERSITE=1` if the package sits in `~/.local`.

## How call targets are found

Every `.py` file is parsed with `ast`. A call is resolved through:

- imports (relative, star, re-exports);
- `self.` / `cls.` / `super()` through the class hierarchy;
- type annotations on parameters, locals and attributes (`Optional[...]` and `Union[...]` included);
- objects created in the same function, or returned by functions with a return annotation;
- `@property`s;
- calling an object, which runs its `__call__` (or `forward` for PyTorch modules).

As a last resort, a method name defined by exactly one class is used. Those edges are labelled
*inferred by method name*.

Static analysis has limits:

- **The declared type, not the runtime one.** A call on a base-class-typed attribute resolves to
  the base class, even if a subclass runs at runtime.
- **Some calls stay unresolved rather than guessed.** This covers dispatch through dictionaries,
  `getattr` strings, `eval`, and decorator or framework magic.
- **Python only.**

## The page

The layout is done in the browser by [ELK](https://eclipse.dev/elk/)'s layered algorithm
([web/layout.js](callgraph/web/layout.js)):

- File boxes are laid out as boxes, and edges are routed around them at right angles.
- A caller's calls appear in order (1, 2, 3 … across the page). Each number gets room reserved on
  its own edge.
- Direction *auto* tries top-down and left-to-right and keeps whichever fits the screen better.
- Big views open at a readable zoom on their entry point, with a minimap to move around.
- Collapsing a box, changing direction and focusing all animate from the old layout to the new one.
- Box titles and call numbers are drawn as small tags above the edges, so lines never run through
  text.

## Checking the layout for overlapping text

```bash
pip install -e '.[check]' && playwright install chromium     # once
callgraph check-layout                                        # build + check every view
callgraph check-layout page.html --direction all --warnings --screenshots out/check
```

This opens each view in headless Chromium, measures what is actually drawn, and reports:

- function text outside its box;
- overlapping boxes;
- titles or call numbers on top of other text;
- call numbers that are missing or not on their own edge;
- (as warnings) edge lines crossing text.

It exits with status 1 when there are errors, so it can run in CI. If Chromium fails to start
because of missing system libraries (`libnss3.so` and similar), run `playwright install-deps`, which
needs root. Without root, install `nss nspr at-spi2-atk at-spi2-core atk-1.0 xorg-libxdamage` from
conda-forge into some prefix and set `LD_LIBRARY_PATH=<prefix>/lib`.

## Use as a library

```python
from callgraph import Index, CallGraph, trace, format_tree

ix = Index("path/to/repo", ["src"])
g = CallGraph(ix)
print(format_tree(g, trace(g, "src/pkg/core.py::Engine.run", depth=2)))
for edge in g.callers("src/pkg/util.py::load"):
    print(edge.caller, edge.line, edge.condition)
```

## Code map

```
callgraph/
  source.py         snapshot of the repo: git archive of a commit, the working tree, or a plain folder
  symbols.py        records for modules, classes, functions, calls
  index.py          ast index of .py and .ipynb files: imports, classes, functions, top-level code, calls
                    with their if/for/while/except context
  resolver.py       which function each call runs
  graph.py          CallGraph: every resolved call            ← `edges`
  trace.py          call trees down/up from one function       ← `trace`
  notebooks.py      Jupyter notebooks as Python source (magics neutralised, cell numbers kept)
  autospec.py       automatic views: source folders, drivers vs library, groups, entry points
  annotate.py       optional local-LLM descriptions (transformers)  ← `annotate`
  specs/            spec loading and defaults
  views/            builder (walks, numbered edges), pipeline (one view), overview
  overlay.py        optional hand-written descriptions
  render.py         inlines web/ + data into one .html         ← `build`
  layout_check.py   headless-Chromium overlap check             ← `check-layout`
  cli.py            the `callgraph` command
  web/              index.html, app.css, app.js, layout.js (ELK), minimap.js, check_overlaps.js
tests/              pytest; fixtures/minirepo is a small repo with one case per resolution rule
```

## Development

```bash
pip install -e '.[dev]'
pytest                 # the browser tests are skipped when Chromium is unavailable
```
