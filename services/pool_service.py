"""Pool fee / prize pot tracker.

Config is per season, stored in the config/settings doc under
``pool_config: {"<season>": {"entry_fee": float, "payouts": [{"place", "amount"}]}}``.
Payouts are dollar amounts (source of truth); ``place`` is a positive int or the
string "last" (last-place money back). Only aggregate counts and the caller's own
paid flag are ever returned; no other player's paid state leaves this module.

A season entry may also carry ``unpaid_visibility`` (see DEFAULT_UNPAID_VISIBILITY) and a
plain-text ``payment_note``; both are optional and read through their own getters so
``get_pool_config`` (which feeds ``/api/pool/status``) is unchanged.
"""
import math

import pandas as pd

from services.data_service import get_latest_week_for_year, get_most_recent_completed_week
from services.db_service import get_config_settings, set_config_settings

DEFAULT_ENTRY_FEE = 200
DEFAULT_PAYOUTS = [{"place": 1, "amount": 1400}, {"place": 2, "amount": 600}]
DEFAULT_UNPAID_VISIBILITY = {"enabled": False, "nudge_week": 8, "public_week": 10, "banner_week": 13}
MAX_PAYMENT_NOTE_LEN = 200


def _default_payouts() -> list:
    return [dict(p) for p in DEFAULT_PAYOUTS]


def _clean_amount(raw):
    """Non-negative finite float, or None if invalid."""
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if math.isnan(val) or math.isinf(val) or val < 0:
        return None
    return val


def _clean_place(raw):
    if isinstance(raw, str) and raw.strip().lower() == "last":
        return "last"
    if isinstance(raw, bool):
        return None
    try:
        place = int(raw)
    except (TypeError, ValueError):
        return None
    return place if place >= 1 else None


def _sort_payouts(payouts: list) -> list:
    return sorted(payouts, key=lambda p: (p["place"] == "last", p["place"] if p["place"] != "last" else 0))


def _clean_payouts(raw):
    """Cleaned, sorted payouts, or None when raw is missing/unusable."""
    if not isinstance(raw, list) or not raw:
        return None
    cleaned, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        place = _clean_place(item.get("place"))
        amount = _clean_amount(item.get("amount"))
        if place is None or amount is None or place in seen:
            continue
        seen.add(place)
        cleaned.append({"place": place, "amount": amount})
    return _sort_payouts(cleaned) if cleaned else None


def get_pool_config(settings: dict, season: int) -> dict:
    """Return the season's pool config, falling back to defaults per missing field. Never raises."""
    entry = None
    try:
        cfg_map = (settings or {}).get("pool_config")
        if isinstance(cfg_map, dict):
            entry = cfg_map.get(str(season))
    except Exception:
        entry = None
    if not isinstance(entry, dict):
        entry = {}
    fee = _clean_amount(entry.get("entry_fee"))
    payouts = _clean_payouts(entry.get("payouts"))
    return {
        "entry_fee": float(DEFAULT_ENTRY_FEE) if fee is None else fee,
        "payouts": _default_payouts() if payouts is None else payouts,
    }


def _default_unpaid_visibility() -> dict:
    return dict(DEFAULT_UNPAID_VISIBILITY)


def _clean_week(raw):
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw if 1 <= raw <= 22 else None


def clean_unpaid_visibility(raw) -> dict:
    """Valid unpaid_visibility dict; anything invalid -> defaults with enabled False. Never raises."""
    if not isinstance(raw, dict):
        return _default_unpaid_visibility()
    nudge, public, banner = (_clean_week(raw.get(k)) for k in ("nudge_week", "public_week", "banner_week"))
    if None in (nudge, public, banner) or not (nudge <= public <= banner):
        return _default_unpaid_visibility()
    return {"enabled": raw.get("enabled") is True, "nudge_week": nudge,
            "public_week": public, "banner_week": banner}


def _season_entry(settings: dict, season: int) -> dict:
    try:
        cfg_map = (settings or {}).get("pool_config")
        entry = cfg_map.get(str(season)) if isinstance(cfg_map, dict) else None
    except Exception:
        entry = None
    return entry if isinstance(entry, dict) else {}


def get_unpaid_visibility(settings: dict, season: int) -> dict:
    return clean_unpaid_visibility(_season_entry(settings, season).get("unpaid_visibility"))


def get_payment_note(settings: dict, season: int) -> str:
    note = _season_entry(settings, season).get("payment_note")
    return note.strip()[:MAX_PAYMENT_NOTE_LEN] if isinstance(note, str) else ""


def has_pool_config(settings: dict, season: int) -> bool:
    cfg_map = (settings or {}).get("pool_config")
    return isinstance(cfg_map, dict) and isinstance(cfg_map.get(str(season)), dict)


def set_pool_config(season: int, entry_fee: float, payouts: list,
                    unpaid_visibility=None, payment_note=None) -> dict:
    """Read-modify-write the whole pool_config map so other seasons are preserved.

    unpaid_visibility / payment_note default to None, meaning "keep what is stored for this season".
    """
    current = get_config_settings() or {}
    existing = current.get("pool_config")
    merged = dict(existing) if isinstance(existing, dict) else {}
    prev = merged.get(str(season)) if isinstance(merged.get(str(season)), dict) else {}
    saved = {
        "entry_fee": float(entry_fee),
        "payouts": _sort_payouts([{"place": p["place"], "amount": float(p["amount"])} for p in payouts]),
    }
    if unpaid_visibility is not None:
        saved["unpaid_visibility"] = clean_unpaid_visibility(unpaid_visibility)
    elif "unpaid_visibility" in prev:
        saved["unpaid_visibility"] = prev["unpaid_visibility"]
    if payment_note is not None:
        saved["payment_note"] = payment_note.strip()[:MAX_PAYMENT_NOTE_LEN]
    elif "payment_note" in prev:
        saved["payment_note"] = prev["payment_note"]
    merged[str(season)] = saved
    set_config_settings({"pool_config": merged})
    return saved


def _label(place) -> str:
    if place == "last":
        return "Last place"
    v = place % 100
    if 10 <= v <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(place % 10, "th")
    return f"{place}{suffix}"


def summarize_pot(fee: float, member_count: int, payouts: list) -> dict:
    total_pot = round(fee * member_count, 2)
    payout_total = round(sum(p["amount"] for p in payouts), 2)
    return {"total_pot": total_pot, "payout_total": payout_total,
            "pot_balance": round(total_pot - payout_total, 2)}


def build_pool_status(order_df, settings: dict, season: int, player_id) -> dict:
    cfg = get_pool_config(settings, season)
    fee = cfg["entry_fee"]

    total = paid = 0
    my_paid = None
    if order_df is not None and not order_df.empty and "season" in order_df.columns:
        rows = order_df[order_df["season"] == season]
        total = int(len(rows))
        if "paid" in rows.columns:
            paid_flags = rows["paid"].fillna(False).astype(bool)
            paid = int(paid_flags.sum())
        else:
            paid_flags = None
        if player_id is not None and "playerId" in rows.columns:
            mask = rows["playerId"] == int(player_id)
            if mask.any():
                my_paid = bool(paid_flags[mask].iloc[0]) if paid_flags is not None else False

    pot = summarize_pot(fee, total, cfg["payouts"])
    return {
        "season": int(season),
        "entry_fee": fee,
        "total_count": total,
        "paid_count": paid,
        "total_pot": pot["total_pot"],
        "collected": round(fee * paid, 2),
        "payouts": [
            {"place": p["place"], "label": _label(p["place"]), "amount": round(p["amount"], 2)}
            for p in cfg["payouts"]
        ],
        "payout_total": pot["payout_total"],
        "pot_balance": pot["pot_balance"],
        "my_paid": my_paid,
    }


def compute_unpaid_stage(current_week, settings) -> str:
    """'off' | 'nudge' | 'public' | 'banner' for the week and an unpaid_visibility dict."""
    vis = clean_unpaid_visibility(settings)
    if not vis["enabled"]:
        return "off"
    try:
        week = int(current_week)
    except (TypeError, ValueError):
        return "off"
    if week >= vis["banner_week"]:
        return "banner"
    if week >= vis["public_week"]:
        return "public"
    if week >= vis["nudge_week"]:
        return "nudge"
    return "off"


def current_played_week(games, season) -> int:
    """The season's current REG week as the standings page shows it (the in-progress
    week, else the last completed one); 0 before any game has a result. Built on the
    existing data_service helpers, which ignore the unplayed schedule (unlike
    get_latest_season_and_week, which returns the schedule's last week). Using the
    in-progress week makes "nudge from week N" begin during week N, not after it."""
    if games is None:
        return 0
    if get_most_recent_completed_week(games, season) is None:
        return 0  # get_latest_week_for_year would report 1 here
    return int(get_latest_week_for_year(games, season))


def _unpaid_members(order_df, players_df, season) -> list:
    if order_df is None or order_df.empty or not {"season", "playerId"} <= set(order_df.columns):
        return []
    rows = order_df[order_df["season"] == season]
    if rows.empty:
        return []
    if "paid" in rows.columns:
        paid = rows["paid"].fillna(False).astype(bool)
    else:
        paid = pd.Series(False, index=rows.index)
    names = {}
    if players_df is not None and not players_df.empty and {"playerId", "fullName"} <= set(players_df.columns):
        names = {int(r.playerId): str(r.fullName) for r in players_df.itertuples() if pd.notna(r.fullName)}
    # One entry per player: a player listed twice in a season's draft_order counts as
    # paid if any of their rows is paid.
    paid_by_player = paid.groupby(rows["playerId"]).any()
    out = [{"playerId": int(pid), "name": names.get(int(pid), f"Player {int(pid)}")}
           for pid, is_paid in paid_by_player.items() if not is_paid]
    return sorted(out, key=lambda r: r["name"].lower())


def build_unpaid_payload(order_df, players_df, settings, season, week, caller_id, is_admin) -> dict:
    """Gated unpaid-entry view. Names leave this function only for admins or in the
    public/banner stages of an enabled season; nudge and off return an empty list."""
    cfg = get_pool_config(settings, season)
    vis = get_unpaid_visibility(settings, season)
    stage = compute_unpaid_stage(week, vis)
    unpaid = _unpaid_members(order_df, players_df, season)
    unpaid_ids = {u["playerId"] for u in unpaid}

    member_ids = set()
    if order_df is not None and not order_df.empty and {"season", "playerId"} <= set(order_df.columns):
        member_ids = {int(p) for p in order_df.loc[order_df["season"] == season, "playerId"]}
    me_member = caller_id is not None and int(caller_id) in member_ids
    me_unpaid = (int(caller_id) in unpaid_ids) if (stage != "off" and me_member) else None

    reveal = bool(is_admin) or (vis["enabled"] and stage in ("public", "banner"))
    show_note = bool(is_admin) or (stage != "off" and me_unpaid is True)
    return {
        "enabled": vis["enabled"],
        "stage": stage,
        "week": int(week),
        "amount": cfg["entry_fee"],
        "unpaid": unpaid if reveal else [],
        "me_unpaid": me_unpaid,
        "payment_note": get_payment_note(settings, season) if show_note else "",
        "admin_view": bool(is_admin),
    }
