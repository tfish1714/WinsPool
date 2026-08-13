"""Analyst consensus projections: derived statistics and model comparison.

Import constraint: this module is imported by routes/admin_routes.py, so it may
depend only on json, logging, pathlib, typing, and numpy. requirements.txt
declares neither scipy nor the ML stack. Spearman correlation is Pearson over
pandas rank values.
"""
import json
import logging
import pathlib
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_REGISTRY_PATH = pathlib.Path(__file__).parent.parent / "data" / "consensus_sources.json"
_registry_cache: Optional[dict] = None


def load_source_registry() -> dict:
    """Return {source_key: {"name": str, "type": str}} from data/consensus_sources.json."""
    global _registry_cache
    if _registry_cache is None:
        with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
            _registry_cache = json.load(f)["sources"]
    return _registry_cache


CANONICAL_SOURCE_KEYS = set(load_source_registry())


def numeric_sources(raw: dict) -> dict:
    """Keep only entries whose value is a real number.

    This is what separates consensus rows from model rows: a model row's sources
    is {'model': 'nn_xgb_lr_ensemble'}, a string, so it yields {}. Testing the
    value type states the intent -- "a source is an analyst who published a
    number" -- rather than hardcoding the key 'model'. bool is excluded because
    it subclasses int but is a flag, not a projection.
    """
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, val in raw.items():
        if isinstance(val, bool):
            continue
        if isinstance(val, (int, float)) and not (isinstance(val, float) and np.isnan(val)):
            out[key] = float(val)
    return out


def compute_derived(sources: dict) -> dict:
    """Summary statistics over one team's per-source projections.

    consensus_std is the population standard deviation, so a single source gives
    0.0 rather than NaN.
    """
    vals = np.array(list(sources.values()), dtype=float)
    if vals.size == 0:
        return {
            "n_sources": 0,
            "consensus_mean": None,
            "consensus_median": None,
            "consensus_min": None,
            "consensus_max": None,
            "consensus_std": None,
        }
    return {
        "n_sources": int(vals.size),
        "consensus_mean": float(np.mean(vals)),
        "consensus_median": float(np.median(vals)),
        "consensus_min": float(np.min(vals)),
        "consensus_max": float(np.max(vals)),
        "consensus_std": float(np.std(vals)),
    }
