"""Pool fee / prize pot tracker.

Pure computation over the draft_order frame and the config settings dict.
Only aggregate counts and the caller's own paid flag are ever returned; no
other player's paid state leaves this module.
"""
import math

DEFAULT_PAYOUTS = [{"place": 1, "pct": 100.0}]


def _clean_fee(raw) -> float:
    try:
        fee = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(fee) or math.isinf(fee) or fee < 0:
        return 0.0
    return fee


def _clean_payouts(raw) -> list:
    if not isinstance(raw, list) or not raw:
        return [dict(p) for p in DEFAULT_PAYOUTS]
    cleaned = []
    for i, item in enumerate(raw):
        try:
            pct = float(item["pct"])
            place = int(item.get("place", i + 1))
        except (TypeError, ValueError, KeyError, AttributeError):
            return [dict(p) for p in DEFAULT_PAYOUTS]
        if math.isnan(pct) or math.isinf(pct) or pct < 0:
            return [dict(p) for p in DEFAULT_PAYOUTS]
        cleaned.append({"place": place, "pct": pct})
    return cleaned


def build_pool_status(order_df, settings: dict, season: int, player_id) -> dict:
    settings = settings or {}
    fee = _clean_fee(settings.get("pool_entry_fee"))
    payouts_cfg = _clean_payouts(settings.get("pool_payouts"))

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

    total_pot = round(fee * total, 2)
    return {
        "season": int(season),
        "entry_fee": fee,
        "total_count": total,
        "paid_count": paid,
        "total_pot": total_pot,
        "collected": round(fee * paid, 2),
        "payouts": [
            {"place": p["place"], "pct": p["pct"], "amount": round(total_pot * p["pct"] / 100, 2)}
            for p in payouts_cfg
        ],
        "my_paid": my_paid,
    }
