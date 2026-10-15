"""The pipeline."""

from app.model.workers import Scorer, SimpleWorker


class Pipeline:
    def __init__(self, cfg):
        self.cfg = cfg
        self.worker = SimpleWorker()

    @property
    def scorer(self) -> Scorer:
        return Scorer()

    def run(self, batch):
        out = self.worker.run(batch)
        return self.scorer(out) if out else None

    def describe(self):
        return self.mystery_object.score_unique_name(1)
