"""
Static call resolution: which in-repo function does a call expression run?

Resolved, in order of preference:

- bare names bound by a ``def``, an import (relative, star, re-exported) or a class (-> ``__init__``);
- ``self.m()`` / ``cls.m()`` / ``super().m()`` through the class's in-repo bases;
- ``obj.m()`` where ``obj`` is a parameter or local with a class annotation, or a local assigned
  from a constructor or from a function whose return is annotated;
- ``self.attr.m()`` / ``self.attr(...)`` where ``attr`` was assigned a known class;
- ``self.prop(x)`` where ``prop`` is a ``@property`` returning a module (-> its ``forward``);
- calling an instance -> its ``__call__``, or ``forward`` for ``nn.Module`` subclasses;
- last resort: a method name defined by exactly one class in the tree (marked ``"inferred"``).

What it cannot see: dispatch through dicts or loop variables, ``getattr`` strings, ``eval``.
Those calls stay unresolved rather than guessed.
"""

import ast
from typing import List, Optional, Tuple

from .index import Index
from .symbols import Call, Func, node_text

# Method names too generic to resolve by name alone (builtins, torch, numpy, logging, ...).
GENERIC_NAMES = frozenset(
    """append extend insert get items keys values update pop popitem copy format join split rsplit strip
    lstrip rstrip replace startswith endswith lower upper to cpu cuda numpy item view reshape permute sum
    mean max min add remove clear sort index count read write close open save load float long int bool
    detach cat stack squeeze unsqueeze flatten apply fit run step zero_grad backward train eval state_dict
    load_state_dict parameters named_parameters modules named_modules children register_buffer info warning
    error debug critical exception exists makedirs dump dumps loads setdefault any all size shape type
    contiguous clone expand repeat split chunk softmax sigmoid argmax argsort topk sort_values iterrows
    groupby merge concat reset_index to_dict tolist astype fill_ zero_ add_ mul_ copy_ requires_grad_
    set_seed result reset reset_state update_state __init__ __call__ forward from_config""".split()
)

INFERRED = "inferred"  # note attached to by-name guesses

Target = Tuple[str, str]  # (func id, note); note is "" for a direct resolution or INFERRED


class Resolver:
    """Resolves calls recorded in an :class:`Index` to function ids."""

    def __init__(self, index: Index) -> None:
        self.ix = index

    # ------------------------------------------------------------------ types of values
    def type_of(self, expr: Optional[ast.expr], f: Optional[Func] = None, recv: Optional[str] = None,
                _d: int = 0, mod=None) -> Optional[str]:
        """Class of the value ``expr`` evaluates to: an annotation, ``Cls(...)``, or ``g(...)`` with
        an annotated return. ``f`` (the enclosing function) and ``recv`` (its ``self`` class) let
        ``g`` itself be resolved like any other call."""
        if expr is None or _d > 3:
            return None
        mod = mod or (self.ix.modules[f.module] if f is not None else None)
        if mod is None:
            return None
        if isinstance(expr, ast.Call):
            k = self.ix.resolve_expr_class(expr.func, mod)
            if k:
                return k
            if f is not None:
                for tid, note in self.resolve_call(Call(expr.func, expr.lineno, expr.col_offset, "", [], []), f, recv, _d + 1):
                    if not note:
                        k = self.returns_class(tid)
                        if k:
                            return k
            return None
        return self.ix.resolve_expr_class(expr, mod)

    def returns_class(self, fid: str) -> Optional[str]:
        """The class named by a function's return annotation."""
        t = self.ix.funcs.get(fid)
        return self.ix.resolve_expr_class(t.node.returns, self.ix.modules[t.module]) if t is not None else None

    def attr_type(self, kid: str, attr: str) -> Optional[str]:
        """Class of ``self.<attr>`` on an instance of ``kid``."""
        for c in self.ix.mro(kid):
            k = self.ix.classes[c]
            e = k.attr_types.get(attr)
            if e is not None:
                r = self.type_of(e, mod=self.ix.modules[k.module])
                if r:
                    return r
        return None

    def callable_entry(self, kid: Optional[str]) -> List[Target]:
        """What calling an instance runs: ``__call__``, or ``forward`` for nn.Modules."""
        if not kid:
            return []
        m = self.ix.find_method(kid, "__call__") or self.ix.find_method(kid, "forward")
        return [(m, "")] if m else []

    def _ctor(self, kid: str) -> List[Target]:
        m = self.ix.find_method(kid, "__init__")
        return [(m, "")] if m else []

    def _by_name(self, name: str) -> List[Target]:
        if name in GENERIC_NAMES or name.startswith("__"):
            return []
        cands = self.ix.methods_by_name.get(name, [])
        return [(cands[0], INFERRED)] if len(cands) == 1 else []

    # ------------------------------------------------------------------ calls
    def resolve_call(self, call: Call, f: Func, self_cls: Optional[str] = None, _d: int = 0) -> List[Target]:
        """Targets of ``call`` made inside ``f``.

        Args:
            call: a call recorded in ``f.calls``.
            f: the function making the call.
            self_cls: what ``self`` really is, when known to be a subclass of ``f``'s class (e.g. a
                concrete subclass running an inherited method). Ignored when not compatible.
        """
        ix = self.ix
        mod = ix.modules[f.module]
        fn = call.func
        own_cls = f.cls
        recv = self_cls if (self_cls and own_cls and own_cls in ix.mro(self_cls)) else own_cls

        if isinstance(fn, ast.Call):  # g(...)(x): call whatever g returns
            return self.callable_entry(self.type_of(fn, f, recv, _d))

        if isinstance(fn, ast.Name):
            if fn.id == "cls" and recv:
                return self._ctor(recv)
            if fn.id in f.local_types and fn.id not in mod.defs:
                return self.callable_entry(self.type_of(f.local_types[fn.id], f, recv, _d))
            r = ix.resolve_name(fn.id, mod)
            if r in ix.funcs:
                return [(r, "")]
            if r in ix.classes:
                return self._ctor(r)
            return []

        if not isinstance(fn, ast.Attribute):
            return []
        base, name = fn.value, fn.attr

        # super().m()
        if isinstance(base, ast.Call) and isinstance(base.func, ast.Name) and base.func.id == "super" and own_cls:
            chain = ix.mro(recv or own_cls)
            start = chain.index(own_cls) + 1 if own_cls in chain else 1
            for c in chain[start:]:
                m = ix.classes[c].methods.get(name)
                if m:
                    return [(m, "")]
            return []

        if isinstance(base, ast.Name):
            if base.id in ("self", "cls") and recv:
                return self._self_attr_call(recv, name)
            if base.id in f.local_types:
                k = self.type_of(f.local_types[base.id], f, recv, _d)
                if k:
                    m = ix.find_method(k, name)
                    return [(m, "")] if m else []
            r = ix.resolve_name(base.id, mod)
            if r in ix.classes:
                m = ix.find_method(r, name)
                return [(m, "")] if m else []
            if base.id in mod.imports:
                r = ix.lookup(f"{mod.imports[base.id]}.{name}")
                if r in ix.funcs:
                    return [(r, "")]
                if r in ix.classes:
                    return self._ctor(r)
                return []
            return self._by_name(name)

        # self.attr.m()
        if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name) and base.value.id == "self" and recv:
            k = self.attr_type(recv, base.attr)
            if k:
                m = ix.find_method(k, name)
                return [(m, "")] if m else []
            return self._by_name(name)

        # a.b.C.m() through a module alias
        dotted = node_text(fn, 200)
        first = dotted.split(".")[0]
        if first in mod.imports:
            r = ix.lookup(mod.imports[first] + dotted[len(first):])
            if r in ix.funcs:
                return [(r, "")]
            if r in ix.classes:
                return self._ctor(r)
        return self._by_name(name)

    def _self_attr_call(self, recv: str, name: str) -> List[Target]:
        """``self.name(...)``: a method, a property returning a module, or an attribute holding one."""
        ix = self.ix
        m = ix.find_method(recv, name)
        if m:
            t = ix.funcs[m]
            if ix.is_property(t):  # self.prop(x) calls what the property returns
                out: List[Target] = []
                for k in ix.resolve_expr_classes(t.node.returns, ix.modules[t.module]):
                    out += [x for x in self.callable_entry(k) if x not in out]
                return out
            return [(m, "")]
        k = self.attr_type(recv, name)
        if k:
            return self.callable_entry(k)
        return self._by_name(name)
