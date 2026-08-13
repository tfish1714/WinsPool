#!/usr/bin/env python3
"""Full preseason refresh for one season, with an attributable before/after diff.

Chains the existing scripts in dependency order, following the STEPS pattern in
scripts/run_cron.py. Prints which teams moved and by how much -- that diff is
the point of the command, not a log detail.

Run twice for attributable deltas:
    python scripts/refresh_preseason.py --season 2026 --skip-sync   # constants only
    python scripts/refresh_preseason.py --season 2026               # + fresh rosters

Usage:
    python scripts/refresh_preseason.py --season 2026
    python scripts/refresh_preseason.py --season 2026 --check-freshness
"""
import argparse
import json
import logging
import pathlib
import pickle
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

ROOT = pathlib.Path(__file__).parent.parent
SCRIPTS_DIR = ROOT / "scripts"
LOCAL_DB = ROOT / ".local_db"
RAWDATA = ROOT / "rawdata"

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"refresh_preseason_{datetime.now():%Y%m%d}.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

NFLVERSE_RELEASES = "https://api.github.com/repos/nflverse/nflverse-data/releases/tags/{tag}"
STALE_AFTER_DAYS = 30

# (release tag, filename template) required by the preseason profile builder.
REQUIRED_ASSETS = [
    ("depth_charts", "depth_charts_{season}.csv", "depth_charts"),
    ("rosters", "roster_{season}.csv", "rosters"),
    ("weekly_rosters", "roster_weekly_{season}.csv", "weekly_rosters"),
    ("snap_counts", "snap_counts_{season}.csv", "snap_counts"),
    ("injuries", "injuries_{season}.csv", "injuries"),
]

STEPS = [
    {"name": "nflverse Raw Data Sync", "script": SCRIPTS_DIR / "sync_nflverse_data.py",
     "args": [], "required": False},
    {"name": "Elo Recompute", "script": SCRIPTS_DIR / "compute_elo.py",
     "args": [], "required": True},
    {"name": "Season Projection", "script": SCRIPTS_DIR / "predict_season.py",
     "args": ["--season", "{season}"], "required": True},
    {"name": "Game Prediction Backfill", "script": SCRIPTS_DIR / "backfill_schedule_predictions.py",
     "args": ["--seasons", "{season}", "{season}", "--firestore", "--force"], "required": True},
    {"name": "Analytics Cache Build", "script": SCRIPTS_DIR / "cache_builder.py",
     "args": ["--year", "{season}", "--force"], "required": False},
    {"name": "Local Mirror Refresh", "script": SCRIPTS_DIR / "refresh_local_pkls.py",
     "args": [], "required": False},
]


def _fetch_release(tag: str) -> dict:
    """GET the nflverse release metadata. urllib, not httpx -- no new dependency."""
    req = urllib.request.Request(
        NFLVERSE_RELEASES.format(tag=tag),
        headers={"User-Agent": "WinsPool/1.0"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def check_asset_freshness(tag: str, filename: str, local_path: pathlib.Path, fetch=None) -> dict:
    """Compare one nflverse asset's remote timestamp against the local file.

    Never raises: a GitHub outage must not block a refresh.
    """
    fetch = fetch or _fetch_release
    out = {
        "tag": tag, "filename": filename,
        "remote_updated_at": None, "local_mtime": None, "status": "unknown",
    }

    if local_path.exists():
        out["local_mtime"] = datetime.fromtimestamp(
            local_path.stat().st_mtime, tz=timezone.utc
        ).isoformat()

    try:
        release = fetch(tag)
    except Exception as e:
        log.warning("  %s: releases API unreachable (%s)", tag, e)
        return out

    asset = next((a for a in release.get("assets", []) if a["name"] == filename), None)
    if asset is None:
        out["status"] = "absent"
        return out

    out["remote_updated_at"] = asset["updated_at"]
    if out["local_mtime"] is None:
        out["status"] = "stale"
    else:
        remote = datetime.fromisoformat(asset["updated_at"].replace("Z", "+00:00"))
        local = datetime.fromisoformat(out["local_mtime"])
        out["status"] = "stale" if remote > local else "current"
    return out


def depth_chart_max_dt(path: pathlib.Path):
    """Latest snapshot timestamp inside the depth-chart CSV.

    A recently downloaded file can still hold only stale snapshots, and the
    profile builder keys off the latest per-player snapshot -- so this, not the
    file mtime, is what determines whether trades are visible.
    """
    if not path.exists():
        return None
    try:
        import pandas as pd
        df = pd.read_csv(path, usecols=["dt"], low_memory=False)
        return str(df["dt"].max())
    except Exception as e:
        log.warning("  could not read dt from %s: %s", path.name, e)
        return None


def run_freshness_preflight(season: int) -> None:
    log.info("-" * 60)
    log.info("Data freshness preflight")
    for tag, template, subdir in REQUIRED_ASSETS:
        filename = template.format(season=season)
        local = RAWDATA / subdir / filename
        res = check_asset_freshness(tag, filename, local)
        log.info("  %-16s %-28s remote=%s local=%s [%s]",
                 tag, filename,
                 res["remote_updated_at"] or "none",
                 (res["local_mtime"] or "none")[:19],
                 res["status"])
        if res["status"] == "absent":
            log.warning("    no 2026 asset published yet -- normal before games are played")

    dc = RAWDATA / "depth_charts" / f"depth_charts_{season}.csv"
    max_dt = depth_chart_max_dt(dc)
    if max_dt:
        log.info("  depth-chart latest snapshot: %s", max_dt)
        try:
            age = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(str(max_dt).replace("Z", "+00:00"))).days
            if age > STALE_AFTER_DAYS:
                log.warning(
                    "    snapshot is %d days old -- profile features will barely move, "
                    "so an empty projection diff is expected", age)
        except ValueError:
            pass
    log.info("-" * 60)


def snapshot_projections(season: int) -> dict:
    """Current mean_wins per team, read from the local mirror."""
    path = LOCAL_DB / f"preseason_predictions_{season}.pkl"
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            df = pickle.load(f)
        return {r["team"]: float(r.get("mean_wins", r.get("projected_wins", 0)))
                for _, r in df.iterrows()}
    except Exception as e:
        log.warning("Could not snapshot projections: %s", e)
        return {}


def diff_projections(before: dict, after: dict) -> list:
    """Per-team before/after, sorted by absolute movement descending."""
    rows = []
    for team in sorted(set(before) | set(after)):
        b, a = before.get(team), after.get(team)
        change = (a - b) if (b is not None and a is not None) else None
        rows.append({"team": team, "before": b, "after": a, "change": change})
    rows.sort(key=lambda r: abs(r["change"]) if r["change"] is not None else 999,
              reverse=True)
    return rows


def run_step(step: dict, season: int) -> bool:
    script = step["script"]
    name = step["name"]
    if not script.exists():
        log.warning("[%s] Script not found: %s -- skipping", name, script)
        return False

    args = [a.format(season=season) for a in step["args"]]
    log.info("[%s] Starting...", name)
    try:
        result = subprocess.run(
            [sys.executable, str(script), *args],
            capture_output=True, text=True, timeout=1800, cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired:
        log.error("[%s] FAILED: timed out after 1800s", name)
        return False

    for line in (result.stdout or "").strip().splitlines():
        log.info("  %s", line)
    for line in (result.stderr or "").strip().splitlines():
        log.warning("  [stderr] %s", line)

    if result.returncode != 0:
        log.error("[%s] FAILED with exit code %d", name, result.returncode)
        return False
    log.info("[%s] Complete", name)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--skip-sync", action="store_true",
                    help="Skip the nflverse sync, isolating non-data changes")
    ap.add_argument("--check-freshness", action="store_true",
                    help="Run the preflight and exit")
    args = ap.parse_args()

    log.info("=" * 60)
    log.info("Preseason refresh -- season %s", args.season)
    log.info("=" * 60)

    run_freshness_preflight(args.season)
    if args.check_freshness:
        return

    before = snapshot_projections(args.season)
    log.info("Snapshotted %d teams before refresh.", len(before))

    for step in STEPS:
        if args.skip_sync and step["name"] == "nflverse Raw Data Sync":
            log.info("[%s] Skipped (--skip-sync)", step["name"])
            continue
        if not run_step(step, args.season) and step["required"]:
            log.error("Required step '%s' failed. Aborting.", step["name"])
            sys.exit(1)

    after = snapshot_projections(args.season)
    rows = diff_projections(before, after)
    moved = [r for r in rows if r["change"] is not None and abs(r["change"]) >= 0.05]

    log.info("=" * 60)
    log.info("Projection changes -- %d of %d teams moved", len(moved), len(rows))
    log.info("%-6s %8s %8s %8s", "TEAM", "BEFORE", "AFTER", "CHANGE")
    for r in rows:
        b = f"{r['before']:.2f}" if r["before"] is not None else "—"
        a = f"{r['after']:.2f}" if r["after"] is not None else "—"
        c = f"{r['change']:+.2f}" if r["change"] is not None else "—"
        log.info("%-6s %8s %8s %8s", r["team"], b, a, c)
    log.info("=" * 60)

    if not moved:
        log.warning("No team moved. Check the preflight above -- if the depth-chart "
                    "snapshot is stale, the roster signal genuinely has not changed.")


if __name__ == "__main__":
    main()
