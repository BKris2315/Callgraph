"""Entry point of the mini repo."""

from app.pipeline import Pipeline
from app.util.io import load_config


def main(debug: bool = False):
    """Load the config and run the pipeline."""
    cfg = load_config("cfg.yaml")
    pipe = Pipeline(cfg)
    if debug:
        pipe.describe()
    for batch in cfg["batches"]:
        pipe.run(batch)
