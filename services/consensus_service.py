"""Analyst consensus projections: derived statistics and model comparison.

Import constraint: this module is imported by routes/admin_routes.py, so it may
depend only on json, logging, pathlib, typing, numpy, and pandas. requirements.txt
declares neither scipy nor the ML stack. Spearman correlation is Pearson over
pandas rank values.
"""
import json
import logging
import pathlib
from typing import Optional

import numpy as np
import pandas as pd

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


def spearman(x, y):
    """Rank correlation, computed as Pearson over ranks.

    scipy is not a declared dependency, so this uses pandas ranking plus
    numpy's corrcoef. Returns None when either series has zero variance.
    """
    if len(x) < 2 or len(y) < 2:
        return None
    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()
    if np.std(rx) == 0 or np.std(ry) == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def _pearson(x, y):
    if len(x) < 2 or np.std(x) == 0 or np.std(y) == 0:
        return None
    return float(np.corrcoef(np.asarray(x, dtype=float), np.asarray(y, dtype=float))[0, 1])


def _dense_rank_desc(values: dict) -> dict:
    """Rank teams by wins, highest first. Ties share the lower rank number."""
    if not values:
        return {}
    s = pd.Series(values)
    return s.rank(ascending=False, method="min").astype(int).to_dict()


def build_comparison(model: dict, consensus: dict, actuals: dict = None) -> dict:
    """Compare model projections against analyst consensus for one season.

    model:     {team: {"mean_wins": float}}   -- from preseason_predictions
    consensus: {team: {"sources": {...}, "consensus_mean": float, ...}}
    actuals:   {team: int} for completed seasons, else None

    When actuals is None the result measures agreement only, never accuracy --
    an in-progress season has no truth to score against.
    """
    if not consensus:
        return {"available": False, "teams": [], "summary": {}, "source_scores": []}

    model_wins = {t: float(v["mean_wins"]) for t, v in model.items()
                  if v.get("mean_wins") is not None}
    cons_center = {t: v["consensus_median"] for t, v in consensus.items()
                   if v.get("consensus_median") is not None}

    model_ranks = _dense_rank_desc(model_wins)
    cons_ranks = _dense_rank_desc(cons_center)

    teams = []
    for team in sorted(set(model_wins) | set(consensus)):
        c = consensus.get(team, {})
        mw = model_wins.get(team)
        median = c.get("consensus_median")
        mean = c.get("consensus_mean")
        std = c.get("consensus_std")

        delta = (mw - median) if (mw is not None and median is not None) else None

        outlier_z = None
        if mw is not None and mean is not None and std:
            outlier_z = (mw - mean) / std

        in_range = None
        if mw is not None and c.get("consensus_min") is not None:
            in_range = bool(c["consensus_min"] <= mw <= c["consensus_max"])

        m_rank = model_ranks.get(team)
        c_rank = cons_ranks.get(team)
        entry = {
            "team": team,
            "model_wins": mw,
            "n_sources": c.get("n_sources", 0),
            "consensus_mean": mean,
            "consensus_median": median,
            "consensus_min": c.get("consensus_min"),
            "consensus_max": c.get("consensus_max"),
            "consensus_std": std,
            "delta": delta,
            "in_range": in_range,
            "outlier_z": outlier_z,
            "model_rank": m_rank,
            "consensus_rank": c_rank,
            "rank_delta": (c_rank - m_rank) if (m_rank and c_rank) else None,
            "actual_wins": None,
            "model_error": None,
            "consensus_error": None,
        }

        if actuals and team in actuals:
            actual = float(actuals[team])
            entry["actual_wins"] = actual
            if mw is not None:
                entry["model_error"] = mw - actual
            if median is not None:
                entry["consensus_error"] = median - actual

        teams.append(entry)

    teams.sort(key=lambda t: abs(t["outlier_z"]) if t["outlier_z"] is not None else -1,
               reverse=True)

    compared = [t for t in teams if t["delta"] is not None]
    deltas = [t["delta"] for t in compared]
    summary = {
        "n_compared": len(compared),
        "mae": float(np.mean(np.abs(deltas))) if deltas else None,
        "bias": float(np.mean(deltas)) if deltas else None,
        "spearman": spearman(
            [t["model_wins"] for t in compared],
            [t["consensus_median"] for t in compared],
        ),
        "n_outside_range": sum(1 for t in compared if t["in_range"] is False),
        "n_delta_over_2": sum(1 for t in compared if abs(t["delta"]) > 2),
        "has_actuals": bool(actuals),
    }

    return {
        "available": True,
        "teams": teams,
        "summary": summary,
        "source_scores": _score_sources(consensus, model_wins, actuals) if actuals else [],
    }


def _score_sources(consensus: dict, model_wins: dict, actuals: dict) -> list:
    """Per-source MAE and Pearson r against actual wins, plus the consensus average.

    Every MAE carries its n: source coverage is uneven across seasons, and a
    figure computed over 64 team-seasons is not comparable to one over 285.
    """
    per_source = {}
    for team, c in consensus.items():
        if team not in actuals:
            continue
        actual = float(actuals[team])
        for src, val in (c.get("sources") or {}).items():
            per_source.setdefault(src, {"pred": [], "act": []})
            per_source[src]["pred"].append(float(val))
            per_source[src]["act"].append(actual)

        if c.get("consensus_mean") is not None:
            per_source.setdefault("consensus_avg", {"pred": [], "act": []})
            per_source["consensus_avg"]["pred"].append(float(c["consensus_mean"]))
            per_source["consensus_avg"]["act"].append(actual)

    model_pred, model_act = [], []
    for team, mw in model_wins.items():
        if team in actuals:
            model_pred.append(mw)
            model_act.append(float(actuals[team]))
    if model_pred:
        per_source["** model **"] = {"pred": model_pred, "act": model_act}

    registry = load_source_registry()
    out = []
    for src, d in per_source.items():
        pred = np.asarray(d["pred"], dtype=float)
        act = np.asarray(d["act"], dtype=float)
        out.append({
            "source": src,
            "name": registry.get(src, {}).get("name", src),
            "mae": float(np.mean(np.abs(pred - act))),
            "r": _pearson(pred, act),
            "n": int(pred.size),
        })
    out.sort(key=lambda s: s["mae"])
    return out
