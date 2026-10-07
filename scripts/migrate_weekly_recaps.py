#!/usr/bin/env python3
"""Fold legacy weekly_recaps/{year}_{week} docs into season_recaps/{year}.

Dry run by default; pass --firestore to write. Idempotent; never deletes the
legacy docs and never overwrites a week that already exists on the season doc.
"""
import argparse
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def migrate(db, write: bool) -> dict:
    by_year = {}
    for doc in db.collection("weekly_recaps").stream():
        d = doc.to_dict() or {}
        if d.get("summary") and d.get("year") is not None and d.get("week") is not None:
            by_year.setdefault(int(d["year"]), {})[int(d["week"])] = d
    report = {y: sorted(w) for y, w in sorted(by_year.items())}
    if not write:
        return report
    for year, weeks in by_year.items():
        ref = db.collection("season_recaps").document(str(year))
        existing = (ref.get().to_dict() or {}).get("weeks", {})
        add = {str(w): {"summary": d["summary"], "timestamp": d.get("timestamp")}
               for w, d in weeks.items() if str(w) not in existing}
        if add:
            ref.set({"year": year, "weeks": add}, merge=True)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--firestore", action="store_true", help="write (default is a dry run)")
    args = ap.parse_args(argv)
    from services.db_service import require_db
    db = require_db()
    report = migrate(db, write=args.firestore)
    for year, weeks in report.items():
        print(f"{year}: weeks {weeks}")
    print("written" if args.firestore else "dry run only (use --firestore to write)")


if __name__ == "__main__":
    main()
