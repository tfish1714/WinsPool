"""Pool fee / prize pot tracker.

Config is per season, stored in the config/settings doc under
``pool_config: {"<season>": {"entry_fee": float, "payouts": [{"place", "amount"}]}}``.
Payouts are dollar amounts (source of truth); ``place`` is a positive int or the
string "last" (last-place money back). Only aggregate counts and the caller's own
paid flag are ever returned; no other player's paid state leaves this module.
"""
import math

from services.db_service import get_config_settings, set_config_settings

DEFAULT_ENTRY_FEE = 200
DEFAULT_PAYOUTS = [{"place": 1, "amount": 1400}, {"place": 2, "amount": 600}]


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


def has_pool_config(settings: dict, season: int) -> bool:
    cfg_map = (settings or {}).get("pool_config")
    return isinstance(cfg_map, dict) and isinstance(cfg_map.get(str(season)), dict)


def set_pool_config(season: int, entry_fee: float, payouts: list) -> dict:
    """Read-modify-write the whole pool_config map so other seasons are preserved."""
    saved = {
        "entry_fee": float(entry_fee),
        "payouts": _sort_payouts([{"place": p["place"], "amount": float(p["amount"])} for p in payouts]),
    }
    current = get_config_settings() or {}
    existing = current.get("pool_config")
    merged = dict(existing) if isinstance(existing, dict) else {}
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
