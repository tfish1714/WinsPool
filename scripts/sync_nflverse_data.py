"""scripts/sync_nflverse_data.py -- Sync rawdata/ from nflverse + nflscraPy releases.

Two data sources:
  - nflverse-data: https://github.com/nflverse/nflverse-data/releases
      Actively maintained, updates nightly during season.
      Covers schedules, team stats, PBP, rosters, injuries, pfr_advstats, etc.

  - nflscraPy: https://github.com/blnkpagelabs/nflscraPy/releases
      Last updated Jan 2024. Used only for SeasonRoster (PFR Approximate Value)
      since nflverse rosters don't include that field.
      NOTE: Stats-*.csv exists here but is not synced — turnovers/3rd-down
      are computed from nflverse weekly stats instead.

Checks release timestamps before downloading to skip unchanged datasets.
Designed to run weekly (Tuesdays after Monday Night Football).

URL patterns:
    nflverse:  https://github.com/nflverse/nflverse-data/releases/download/{tag}/{file}
    nflscraPy: https://github.com/blnkpagelabs/nflscraPy/releases/download/{tag}/{file}

Usage:
    python scripts/sync_nflverse_data.py                     # current season
    python scripts/sync_nflverse_data.py --season 2025       # explicit season
    python scripts/sync_nflverse_data.py --seasons 2023 2025 # range
    python scripts/sync_nflverse_data.py --include-pbp       # include large pbp files (~100MB each)
    python scripts/sync_nflverse_data.py --force             # ignore timestamps, re-download all
    python scripts/sync_nflverse_data.py --dry-run           # show what would be downloaded
"""

import argparse
import gzip
import json
import logging
import pathlib
import shutil
import sys
import time
import urllib.request
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from services.db_service import save_metadata

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

RAWDATA_DIR = pathlib.Path(__file__).parent.parent / "rawdata"
TIMESTAMP_CACHE = RAWDATA_DIR / ".sync_timestamps.json"
BASE_URL = "https://github.com/nflverse/nflverse-data/releases/download"
NFLSCRAPY_BASE_URL = "https://github.com/blnkpagelabs/nflscraPy/releases/download"

# Current NFL season — update this each year or derive from date
import datetime as _dt
_today = _dt.date.today()
CURRENT_SEASON = _today.year if _today.month >= 9 else _today.year - 1


# ---------------------------------------------------------------------------
# Release definitions
# ---------------------------------------------------------------------------
# Each entry maps a release tag to a list of (remote_filename, local_path) pairs.
# {year} is replaced with each season being synced.
# Files with no {year} are single-file releases (downloaded once regardless of season).
#
# Release tags: https://github.com/nflverse/nflverse-data/releases

RELEASES = {
    # Schedule + results — single file, updated constantly during season
    "schedules": {
        "files": [
            ("games.csv", "schedules/games.csv"),
        ],
        "year_specific": False,
        "priority": 1,   # always sync
    },

    # Team weekly stats — what we use for rolling EPA (no leakage)
    "stats_team": {
        "files": [
            ("stats_team_reg_{year}.csv", "stats_team/stats_team_reg_{year}.csv"),
            ("stats_team_week_{year}.csv", "stats_team/stats_team_week_{year}.csv"),
        ],
        "year_specific": True,
        "priority": 1,
    },

    # Rosters — used for roster talent / trench features
    "rosters": {
        "files": [
            ("roster_{year}.csv", "rosters/roster_{year}.csv"),
        ],
        "year_specific": True,
        "priority": 2,
    },

    # PFR advanced weekly stats (pressure rate, hurry rate, drop %, bad throw %)
    # nflverse only has this from 2018 onward
    "pfr_advanced": {
        "files": [
            ("advstats_week_pass_{year}.csv", "pfr_advstats/advstats_week_pass_{year}.csv"),
            ("advstats_week_def_{year}.csv",  "pfr_advstats/advstats_week_def_{year}.csv"),
            ("advstats_week_rush_{year}.csv", "pfr_advstats/advstats_week_rush_{year}.csv"),
            ("advstats_week_rec_{year}.csv",  "pfr_advstats/advstats_week_rec_{year}.csv"),
        ],
        "year_specific": True,
        "min_year": 2018,
        "priority": 2,
    },

    # FTN charting (2022+ only)
    "ftn_charting": {
        "files": [
            ("ftn_charting_{year}.csv", "ftn_charting/ftn_charting_{year}.csv"),
        ],
        "year_specific": True,
        "min_year": 2022,
        "priority": 3,
    },

    # Depth charts
    "depth_charts": {
        "files": [
            ("depth_charts_{year}.csv", "depth_charts/depth_charts_{year}.csv"),
        ],
        "year_specific": True,
        "priority": 3,
    },

    # Snap counts
    "snap_counts": {
        "files": [
            ("snap_counts_{year}.csv", "snap_counts/snap_counts_{year}.csv"),
        ],
        "year_specific": True,
        "priority": 3,
    },

    # Injuries
    "injuries": {
        "files": [
            ("injuries_{year}.csv", "injuries/injuries_{year}.csv"),
        ],
        "year_specific": True,
        "priority": 3,
    },

    # Weekly rosters (week-level, for in-season roster changes)
    "weekly_rosters": {
        "files": [
            ("roster_weekly_{year}.csv", "weekly_rosters/roster_weekly_{year}.csv"),
        ],
        "year_specific": True,
        "priority": 3,
    },

    # Play-by-play — large (~100MB compressed per season), opt-in only
    "pbp": {
        "files": [
            # Downloaded as .gz, decompressed to .csv
            ("play_by_play_{year}.csv.gz", "pbp/play_by_play_{year}.csv"),
        ],
        "year_specific": True,
        "compressed": True,   # .gz → decompress on download
        "priority": 99,       # skipped unless --include-pbp
    },
}

# nflscraPy releases — separate base URL, no timestamp files.
# Last updated Jan 2024; historical data only (don't expect 2025+ updates).
# Only syncing SeasonRoster since it has PFR Approximate Value not in nflverse.
NFLSCRAPY_RELEASES = {
    "SeasonRoster": {
        "files": [
            ("SeasonRoster-{year}.csv", "SeasonRoster-{year}.csv"),
        ],
        "year_specific": True,
        "max_year": 2023,  # nflscraPy not updated beyond this
        "priority": 2,
    },
}


# ---------------------------------------------------------------------------
# Timestamp cache
# ---------------------------------------------------------------------------

def _load_timestamp_cache() -> dict:
    if TIMESTAMP_CACHE.exists():
        return json.loads(TIMESTAMP_CACHE.read_text())
    return {}


def _save_timestamp_cache(cache: dict):
    RAWDATA_DIR.mkdir(parents=True, exist_ok=True)
    TIMESTAMP_CACHE.write_text(json.dumps(cache, indent=2))


def _fetch_release_timestamp(tag: str) -> Optional[str]:
    """Fetch the release timestamp from nflverse to detect if data has changed."""
    url = f"{BASE_URL}/{tag}/timestamp.txt"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.read().decode().strip()
    except Exception as e:
        logger.debug("Could not fetch timestamp for %s: %s", tag, e)
        return None


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _download_file(url: str, dest: pathlib.Path, compressed: bool = False,
                   dry_run: bool = False) -> bool:
    """Download a file from url to dest. Handles .gz decompression.

    Returns True if the file was written, False on failure.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dry_run:
        print(f"    [DRY RUN] Would download: {url}")
        print(f"             → {dest}")
        return True

    tmp = dest.with_suffix(".tmp")
    try:
        logger.debug("  Fetching %s", url)
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = resp.read()

        if compressed:
            data = gzip.decompress(data)

        tmp.write_bytes(data)
        shutil.move(str(tmp), str(dest))
        return True

    except Exception as e:
        logger.warning("  Failed to download %s: %s", url, e)
        if tmp.exists():
            tmp.unlink()
        return False


# ---------------------------------------------------------------------------
# Main sync logic
# ---------------------------------------------------------------------------

def sync(
    seasons: list[int],
    include_pbp: bool = False,
    force: bool = False,
    dry_run: bool = False,
    max_priority: int = 3,
) -> dict:
    """Sync rawdata/ for the given seasons.

    Returns a summary dict: {tag: {"downloaded": N, "skipped": N, "failed": N}}
    """
    cache = _load_timestamp_cache()
    summary = {}
    total_downloaded = 0
    total_skipped = 0
    total_failed = 0

    for tag, cfg in RELEASES.items():
        priority = cfg.get("priority", 3)
        if priority == 99 and not include_pbp:
            continue
        if priority > max_priority:
            continue

        print(f"\n[{tag}]")

        # Check release timestamp
        remote_ts = _fetch_release_timestamp(tag)
        cached_ts = cache.get(tag)

        if remote_ts and remote_ts == cached_ts and not force:
            print(f"  Up to date (last synced: {cached_ts})")
            summary[tag] = {"downloaded": 0, "skipped": "up-to-date", "failed": 0}
            total_skipped += 1
            continue

        if remote_ts:
            print(f"  New data available: {remote_ts}")
        else:
            print(f"  Could not check timestamp — attempting download")

        downloaded = 0
        failed = 0
        year_list = seasons if cfg.get("year_specific") else [None]
        min_year = cfg.get("min_year", 0)

        for year in year_list:
            if year is not None and year < min_year:
                continue

            for remote_tpl, local_tpl in cfg["files"]:
                remote_name = remote_tpl.replace("{year}", str(year)) if year else remote_tpl
                local_rel = local_tpl.replace("{year}", str(year)) if year else local_tpl
                dest = RAWDATA_DIR / local_rel
                url = f"{BASE_URL}/{tag}/{remote_name}"

                print(f"  >> {remote_name}")
                ok = _download_file(
                    url, dest,
                    compressed=cfg.get("compressed", False),
                    dry_run=dry_run,
                )
                if ok:
                    downloaded += 1
                else:
                    failed += 1

        if downloaded > 0 and not dry_run and remote_ts:
            cache[tag] = remote_ts
            _save_timestamp_cache(cache)

        summary[tag] = {"downloaded": downloaded, "failed": failed}
        total_downloaded += downloaded
        total_failed += failed

    # --- nflscraPy (no timestamp API, check local file existence only) ---
    for tag, cfg in NFLSCRAPY_RELEASES.items():
        priority = cfg.get("priority", 3)
        if priority > max_priority:
            continue

        print(f"\n[nflscraPy/{tag}]")
        downloaded = 0
        failed = 0
        max_year = cfg.get("max_year", CURRENT_SEASON)
        year_list = [y for y in (seasons if cfg.get("year_specific") else [None])
                     if y is None or y <= max_year]

        for year in year_list:
            for remote_tpl, local_tpl in cfg["files"]:
                remote_name = remote_tpl.replace("{year}", str(year)) if year else remote_tpl
                local_rel = local_tpl.replace("{year}", str(year)) if year else local_tpl
                dest = RAWDATA_DIR / local_rel
                url = f"{NFLSCRAPY_BASE_URL}/{tag}/{remote_name}"

                # nflscraPy has no timestamps — skip if file already exists
                if dest.exists() and not force:
                    continue

                print(f"  >> {remote_name}")
                ok = _download_file(url, dest, compressed=False, dry_run=dry_run)
                if ok:
                    downloaded += 1
                else:
                    failed += 1

        if downloaded == 0 and failed == 0:
            print("  All files present (skipped)")
            total_skipped += 1
        summary[f"nflscraPy/{tag}"] = {"downloaded": downloaded, "failed": failed}
        total_downloaded += downloaded
        total_failed += failed

    return summary, total_downloaded, total_skipped, total_failed


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Sync nflverse rawdata to rawdata/ directory"
    )
    parser.add_argument(
        "--season", type=int, default=None,
        help=f"Single season to sync (default: current season {CURRENT_SEASON})"
    )
    parser.add_argument(
        "--seasons", type=int, nargs=2, metavar=("FROM", "TO"),
        help="Season range to sync, e.g. --seasons 2020 2025"
    )
    parser.add_argument(
        "--include-pbp", action="store_true",
        help="Also download play-by-play files (~100MB each, slow)"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Ignore timestamps and re-download all files"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be downloaded without fetching"
    )
    parser.add_argument(
        "--priority", type=int, default=3,
        help="Max priority level to sync (1=core only, 2=+pfr/rosters, 3=all; default: 3)"
    )
    args = parser.parse_args()

    # Determine seasons to sync
    if args.seasons:
        seasons = list(range(args.seasons[0], args.seasons[1] + 1))
    elif args.season:
        seasons = [args.season]
    else:
        # Default: current season + previous (to catch any late updates)
        seasons = [CURRENT_SEASON - 1, CURRENT_SEASON]

    start = time.time()
    print("=" * 65)
    print("  nflverse Data Sync")
    print(f"  Seasons: {seasons}")
    print(f"  Rawdata: {RAWDATA_DIR}")
    if args.dry_run:
        print("  MODE: DRY RUN — no files will be written")
    print("=" * 65)

    summary, n_dl, n_skip, n_fail = sync(
        seasons=seasons,
        include_pbp=args.include_pbp,
        force=args.force,
        dry_run=args.dry_run,
        max_priority=args.priority,
    )

    elapsed = time.time() - start
    print(f"\n{'='*65}")
    print(f"  Sync complete ({elapsed:.1f}s)")
    print(f"  Downloaded: {n_dl} files")
    print(f"  Up-to-date: {n_skip} releases skipped")
    print(f"  Failed:     {n_fail} files")
    if n_fail > 0:
        print("  NOTE: Failures may indicate files not yet available for this season.")
    print(f"{'='*65}")

    try:
        save_metadata("sync_nflverse", {
            "completed_at": time.time(),
            "season": max(seasons),
            "datasets_synced": n_dl,
            "datasets_skipped": n_skip,
            "datasets_failed": n_fail,
            "status": "ok" if n_fail == 0 else "error",
            "error": None,
        })
    except Exception as _e:
        print(f"  Warning: could not write sync metadata: {_e}")

    sys.exit(1 if n_fail > 0 else 0)


if __name__ == "__main__":
    main()
