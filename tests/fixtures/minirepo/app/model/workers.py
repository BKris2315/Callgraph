"""Concrete parts."""

from typing import Optional

from ..util import log
from .base import Base, register


class Scorer:
    """Callable helper."""

    def __call__(self, y):
        return self.score_unique_name(y)

    def score_unique_name(self, y):
        return y


@register("simple")
class SimpleWorker(Base):
    """A worker."""

    def __init__(self, scorer: Optional[Scorer] = None):
        self.scorer = scorer or Scorer()
        self.extra = Scorer()

    def prepare(self, x):
        x = super().prepare(x)
        log("prepared")
        return x

    def step(self, y):
        try:
            return self.extra(y)
        except ValueError:
            return self.fallback(y)

    def fallback(self, y):
        return y
