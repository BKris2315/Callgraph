"""
Optional: let a local language model write the descriptions an overlay holds.

``callgraph annotate`` builds the page as usual, asks a model running on your own machine
(any Hugging Face ``transformers`` chat model: Qwen, Gemma, Mistral, Llama, ...) to describe each
function and view, and writes the result as an overlay file that ``callgraph build --overlay``
shows on the page. Nothing leaves the machine. The core tool does not need any of this: the model
libraries are an optional extra (``pip install -e '.[llm]'``) and are imported only here.

Determinism
-----------
- **Greedy decoding** (no sampling), fixed prompts, and **one prompt at a time**: each text depends
  only on its own prompt, so the same model, code and hardware give the same text. (Batching pads
  prompts to a common length, which changes the floating-point arithmetic slightly, and greedy
  decoding can then take another word; ``batch_size > 1`` is faster but not byte-stable.)
- **Cache**: every generated entry records the function's body hash, the model and the prompt
  version. A re-run keeps entries whose function has not changed and regenerates only the rest, so
  descriptions do not drift between runs. ``--refresh`` regenerates everything.
- **People win**: entries without a ``model`` field were written by a person and are never
  replaced.

On the page, a generated description carries a note naming the model, so readers know it was not
written by a person.
"""

import ast
import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .graph import CallGraph, short_label
from .index import Index

DEFAULT_MODEL = "Qwen/Qwen3-4B-Instruct-2507"
PROMPT_VERSION = 2  # bump when prompts change: cached text from older prompts is regenerated
MAX_SOURCE_LINES = 80
MAX_CHARS = 420

FUNCTION_SYSTEM = (
    "You document a Python codebase for developers who are new to it. Given one function, write one or two "
    "plain sentences saying what it does and what it is for. Start with a verb (for example 'Builds …', 'Checks …'), "
    "not with 'This function'. Mention important side effects or conditions. Do not restate the function's name, "
    "do not use markdown or code formatting, do not speculate beyond the code."
)
VIEW_SYSTEM = (
    "You document a Python codebase for developers who are new to it. Given one part of the code (its entry "
    "points and the functions they call, with short descriptions), write two or three plain sentences explaining "
    "what this part does and how the flow goes. No markdown, no lists, do not speculate beyond what is given."
)

Messages = List[Dict[str, str]]


# ============================================================================ model backends
class Backend:
    """Turns chat prompts into text. ``name`` identifies the model in the overlay."""

    name = "backend"
    revision = ""

    def generate(self, prompts: Sequence[Messages]) -> List[str]:  # pragma: no cover - interface
        raise NotImplementedError


class TransformersBackend(Backend):
    """A local Hugging Face ``transformers`` chat model, greedy decoding.

    Args:
        model: Hub id or local folder, e.g. ``Qwen/Qwen3-4B-Instruct-2507``.
        device: ``auto`` (spread over all GPUs, else CPU), ``cpu``, ``cuda`` or ``cuda:1``.
        dtype: ``auto`` (bfloat16 on GPUs that support it, else float16 on GPU, float32 on CPU),
            ``bfloat16``, ``float16`` or ``float32``.
        max_new_tokens: generation budget per answer.
        batch_size: prompts generated together. 1 (default) is reproducible; larger is faster but the
            text can then depend on which prompts share a batch.
    """

    def __init__(self, model: str = DEFAULT_MODEL, device: str = "auto", dtype: str = "auto",
                 max_new_tokens: int = 160, batch_size: int = 1) -> None:
        try:
            import torch
            import transformers
        except ImportError as e:
            raise RuntimeError("annotate needs the optional model libraries: pip install -e '.[llm]' "
                               "(transformers, torch, accelerate)") from e
        self.torch, self.name = torch, model
        self.max_new_tokens, self.batch_size = max_new_tokens, batch_size
        cuda = torch.cuda.is_available() and not device.startswith("cpu")
        if dtype == "auto":
            dt = (torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16) if cuda else torch.float32
        else:
            dt = getattr(torch, dtype)
        device_map = ("auto" if cuda else None) if device == "auto" else device
        try:
            self.tok = transformers.AutoTokenizer.from_pretrained(model)
        except Exception as e:
            raise RuntimeError(f"could not load the tokenizer of {model}: {type(e).__name__}: {str(e).splitlines()[0]}") from e
        self.tok.padding_side = "left"  # decoder-only batching
        if self.tok.pad_token is None:
            self.tok.pad_token = self.tok.eos_token
        kwargs = dict(dtype=dt, device_map=device_map)
        try:
            try:
                self.model = transformers.AutoModelForCausalLM.from_pretrained(model, **kwargs)
            except ValueError:  # multimodal chat models (e.g. Gemma 3 4B+) answer text through this class
                self.model = transformers.AutoModelForImageTextToText.from_pretrained(model, **kwargs)
        except Exception as e:  # gated model, no network, out of memory, a broken package in the environment, ...
            hint = ""
            if isinstance(e, ImportError):
                hint = (" A package in this Python environment does not match the installed torch; a clean virtualenv "
                        "(pip install -e '.[llm]'), or PYTHONNOUSERSITE=1 if the package sits in ~/.local, avoids it.")
            elif "gated" in str(e).lower() or "401" in str(e) or "403" in str(e):
                hint = " The model is gated: accept its licence on huggingface.co and run `huggingface-cli login`."
            raise RuntimeError(f"could not load {model}: {type(e).__name__}: {str(e).splitlines()[0]}{hint}") from e
        if device_map is None:
            self.model.to("cpu")
        self.model.eval()
        self.revision = getattr(self.model.config, "_commit_hash", "") or ""

    def _render(self, messages: Messages) -> str:
        if getattr(self.tok, "chat_template", None):
            try:  # models with a thinking mode (Qwen3, ...): answer directly
                return self.tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
            except TypeError:
                return self.tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        return "\n\n".join(m["content"] for m in messages) + "\n\nAnswer:"

    def generate(self, prompts: Sequence[Messages]) -> List[str]:
        out: List[str] = []
        torch = self.torch
        for i in range(0, len(prompts), self.batch_size):
            texts = [self._render(m) for m in prompts[i:i + self.batch_size]]
            enc = self.tok(texts, return_tensors="pt", padding=True, add_special_tokens=False).to(self.model.device)
            with torch.inference_mode():
                gen = self.model.generate(**enc, do_sample=False, num_beams=1, max_new_tokens=self.max_new_tokens,
                                          pad_token_id=self.tok.pad_token_id, temperature=None, top_p=None, top_k=None)
            out += self.tok.batch_decode(gen[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)
        return out


# ============================================================================ prompts
def clean(text: str, limit: int = MAX_CHARS) -> str:
    """Model output -> one plain paragraph: no thinking blocks, markdown, labels or surrounding quotes."""
    text = re.sub(r"<think>.*?</think>", " ", text, flags=re.S)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"^\s*(description|answer|summary)\s*:\s*", "", text, flags=re.I)
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)  # **bold** / __bold__
    text = re.sub(r"(?<!\w)[*_](\S.*?)[*_](?!\w)", r"\1", text)  # *italic* / _italic_, not snake_case
    text = re.sub(r"[`#>]+", "", text)
    text = re.sub(r"^(this|the) (function|method)\s+", "", text, flags=re.I)
    text = text[:1].upper() + text[1:]
    text = re.sub(r"\s+", " ", text).strip().strip('"').strip()
    if len(text) > limit:  # cut at the last full sentence that fits
        cut = text[:limit]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        text = cut[: end + 1] if end > limit // 3 else cut.rstrip() + "…"
    return text


def function_source(ix: Index, fid: str, max_lines: int = MAX_SOURCE_LINES) -> str:
    f = ix.funcs[fid]
    try:
        with open(os.path.join(ix.root, f.path), encoding="utf-8") as fh:
            src = ast.get_source_segment(fh.read(), f.node) or ""
    except OSError:
        src = ""
    lines = src.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"    # … {len(lines) - max_lines} more lines"]
    return "\n".join(lines)


def function_prompt(ix: Index, graph: CallGraph, fid: str) -> Messages:
    f = ix.funcs[fid]
    calls = [short_label(ix, e.callee) for e in graph.callees(fid)][:15]
    callers = [short_label(ix, e.caller) for e in graph.callers(fid)][:10]
    owner = ix.classes[f.cls] if f.cls else None
    parts = [f"File: {f.path}", f"Function: {f.qual}"]
    if owner and owner.doc:
        parts.append(f"Its class: {owner.name}: {owner.doc}")
    if f.doc:
        parts.append(f"Its docstring: {f.doc}")
    parts.append("It calls: " + (", ".join(calls) if calls else "nothing else in this repository"))
    parts.append("Called by: " + (", ".join(callers) if callers else "nothing in this repository (an entry point)"))
    parts.append("Source:\n" + function_source(ix, fid))
    return [{"role": "system", "content": FUNCTION_SYSTEM}, {"role": "user", "content": "\n".join(parts)}]


def view_prompt(view: dict, descs: Dict[str, str]) -> Messages:
    lines = [f"Part of the code: {view['title']}"]
    roots = [n for n in view["nodes"] if not any(e[1] == n["id"] for e in view["edges"])]
    lines.append("Entry points: " + ", ".join(n["label"] for n in roots[:8]))
    lines.append("Functions:")
    for n in view["nodes"][:30]:
        d = descs.get(n["id"]) or (n["desc"] if n["desc"] != "(no docstring)" else "")
        lines.append(f"- {n['label']} ({n['loc']})" + (f": {d}" if d else ""))
    return [{"role": "system", "content": VIEW_SYSTEM}, {"role": "user", "content": "\n".join(lines)}]


def _prompt_hash(messages: Messages) -> str:
    return hashlib.sha1(repr(messages).encode()).hexdigest()[:12]


# ============================================================================ annotate
@dataclass
class AnnotateStats:
    generated: int = 0
    kept: int = 0  # cached model text still valid
    human: int = 0  # hand-written entries left alone
    views: int = 0
    seconds: float = 0.0
    skipped_documented: int = 0
    targets: List[str] = field(default_factory=list)


def annotate(ix: Index, graph: CallGraph, page_data: dict, backend: Backend, existing: Optional[dict] = None,
             commit: str = "", only_missing: bool = True, views: bool = True, refresh: bool = False,
             limit: Optional[int] = None, progress: Callable[[str], None] = lambda s: None) -> Tuple[dict, AnnotateStats]:
    """Describe the functions (and views) on a page; return ``(overlay, stats)``.

    Args:
        page_data: from :func:`callgraph.views.build_page`; decides which functions are described.
        backend: the model.
        existing: a previous overlay (cache, and hand-written entries to keep).
        only_missing: describe only functions without a docstring (else all of them).
        views: also write an intro for every view.
        refresh: ignore cached model text.
        limit: describe at most this many functions (for a quick try).
    """
    existing = existing or {}
    overlay = {"generator": {"model": backend.name, "revision": backend.revision, "prompt_version": PROMPT_VERSION},
               "commit": commit, "views": {}, "nodes": {}, "overview_nodes": dict(existing.get("overview_nodes", {}))}
    stats = AnnotateStats()
    t0 = time.time()

    on_page = []
    for vid, v in page_data["views"].items():
        if vid == "overview":
            continue
        on_page += [n["id"] for n in v["nodes"] if n["id"] in ix.funcs]
    on_page = sorted(dict.fromkeys(on_page))  # fixed order: same input, same batches

    old_nodes = existing.get("nodes", {})
    todo = []
    for fid in on_page:
        f = ix.funcs[fid]
        old = old_nodes.get(fid)
        if old and not old.get("model"):
            overlay["nodes"][fid] = old
            stats.human += 1
            continue
        if only_missing and f.doc:
            stats.skipped_documented += 1
            continue
        fresh = (old and not refresh and old.get("model") == backend.name and old.get("body_hash") == f.body_hash
                 and old.get("prompt_version") == PROMPT_VERSION)
        if fresh:
            overlay["nodes"][fid] = old
            stats.kept += 1
            continue
        todo.append(fid)
    if limit is not None:
        todo = todo[:limit]
    stats.targets = todo

    if todo:
        progress(f"describing {len(todo)} functions with {backend.name} …")
        answers = backend.generate([function_prompt(ix, graph, fid) for fid in todo])
        for fid, text in zip(todo, answers):
            text = clean(text)
            if not text:
                continue
            overlay["nodes"][fid] = {"desc": text, "body_hash": ix.funcs[fid].body_hash, "commit": commit,
                                     "model": backend.name, "prompt_version": PROMPT_VERSION}
            stats.generated += 1

    # keep entries for functions not on this page (another spec may show them)
    for fid, old in old_nodes.items():
        overlay["nodes"].setdefault(fid, old)

    if views:
        descs = {fid: e["desc"] for fid, e in overlay["nodes"].items()}
        vids, prompts = [], []
        for vid, v in page_data["views"].items():
            if vid == "overview":
                continue
            old = existing.get("views", {}).get(vid)
            prompt = view_prompt(v, descs)
            if old and not old.get("model"):
                overlay["views"][vid] = old
            elif (old and not refresh and old.get("model") == backend.name and old.get("prompt_hash") == _prompt_hash(prompt)
                  and old.get("prompt_version") == PROMPT_VERSION):
                overlay["views"][vid] = old
            else:
                vids.append(vid)
                prompts.append(prompt)
        if prompts:
            progress(f"writing {len(prompts)} view introductions …")
            for vid, text, prompt in zip(vids, backend.generate(prompts), prompts):
                text = clean(text, 700)
                if text:
                    overlay["views"][vid] = {"intro": [text], "commit": commit, "model": backend.name,
                                             "prompt_hash": _prompt_hash(prompt), "prompt_version": PROMPT_VERSION}
                    stats.views += 1
        for vid, old in existing.get("views", {}).items():
            overlay["views"].setdefault(vid, old)
    stats.seconds = round(time.time() - t0, 1)
    return overlay, stats
