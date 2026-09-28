"""services/live_window_service.py -- pure "is an NFL game window currently
open" check, shared by scripts/sync_live_scores.py's job fast-exit and
services/data_service.py's dynamic remote-check interval.

No I/O or network dependencies: operates solely on an already-loaded games
DataFrame with gameday/gametime/result columns. Fails open (True) on any
error or empty/missing input, so a malformed or absent games frame can never
suppress live-score freshness.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo("America/New_York")
WINDOW_LEAD = timedelta(minutes=20)         # open this long before the earliest kickoff
WINDOW_FINAL_TAIL = timedelta(minutes=30)   # close after the last kickoff once every game is final
WINDOW_LIVE_TAIL = timedelta(hours=4, minutes=30)  # 3h game + 1.5h overtime/delay buffer
CARRYOVER = timedelta(hours=5)              # yesterday's kickoffs still in play past midnight


def is_within_live_window(games: pd.DataFrame, now_et: datetime | None = None) -> bool:
    """True when `now_et` (default: now, America/New_York) falls inside an NFL
    game window implied by `games`.

    Candidate games are today's (ET) plus yesterday's whose kickoff is within
    the last 5 hours (a late game still running past midnight). The window
    opens 20 minutes before the earliest candidate kickoff and closes 30
    minutes after the latest kickoff once every candidate has a result, else
    4.5 hours after it. Fails open (True) on any error or empty/missing
    input, so a broken or not-yet-loaded games frame can never suppress live
    updates."""
    try:
        now = now_et or datetime.now(ET)
        if games is None or games.empty:
            return True
        df = games.dropna(subset=["gameday", "gametime"]).copy()
        if df.empty:
            return False
        parsed = pd.to_datetime(
            df["gameday"].astype(str) + " " + df["gametime"].astype(str), errors="coerce"
        )
        df["kickoff"] = [
            k.to_pydatetime().replace(tzinfo=ET) if pd.notna(k) else None for k in parsed
        ]
        df = df[df["kickoff"].notna()]
        today = now.date()
        keep = [
            k.date() == today
            or (k.date() == today - timedelta(days=1) and now - k <= CARRYOVER)
            for k in df["kickoff"]
        ]
        df = df[keep]
        if df.empty:
            return False
        start = min(df["kickoff"]) - WINDOW_LEAD
        all_final = bool(df["result"].notna().all())
        end = max(df["kickoff"]) + (WINDOW_FINAL_TAIL if all_final else WINDOW_LIVE_TAIL)
        return start <= now <= end
    except Exception:
        return True
