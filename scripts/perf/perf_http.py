"""Server-side timing of the prod site, no browser needed.

For each path: TTFB (time to response headers), total time, size, compression,
cache headers -- repeated N times. The first request is reported separately
because on Cloud Run it can include a cold start (scale-to-zero).

    python scripts/perf/perf_http.py                       # public paths only
    python scripts/perf/perf_http.py -n 8 --url https://...
    python scripts/perf/perf_http.py --cold --idle 900     # idle first so the instance may scale down
    WINSPOOL_SESSION_TOKEN=... python scripts/perf/perf_http.py   # authed pages/APIs

Appends a summary line to reports/perf_history.jsonl for trend comparison.
"""
import argparse
import statistics as st
import time

import requests

from common import append_history, base_url, login

PUBLIC = [
    "/",
    "/static/style.css?v=27",
    "/static/js/main.js?v=10",
    "/api/config/settings",
    "/mock-draft",
]
AUTHED = [
    "/wins-pool",
    "/api/live-standings?year=2026",
    "/schedule",
    "/playoff-race",
]


def measure(sess, url, headers):
    t0 = time.perf_counter()
    r = sess.get(url, headers=headers, stream=True, timeout=120, allow_redirects=True)
    ttfb = (time.perf_counter() - t0) * 1000  # headers received
    body = r.raw.read(decode_content=False)  # wire bytes
    total = (time.perf_counter() - t0) * 1000
    return {
        "status": r.status_code, "ttfb": ttfb, "total": total, "wire": len(body),
        "enc": r.headers.get("content-encoding", "none"),
        "cache": r.headers.get("cache-control", "-"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("-n", type=int, default=5, help="requests per path after the first")
    ap.add_argument("--cold", action="store_true", help="idle first to provoke a cold start")
    ap.add_argument("--idle", type=int, default=900, help="seconds to idle with --cold")
    a = ap.parse_args()
    url = base_url(a.url)

    sess = requests.Session()
    headers = {"Accept-Encoding": "gzip, br"}
    token = login(sess, url)
    paths = PUBLIC + (AUTHED if token else [])
    if token:
        headers["Cookie"] = f"session_token={token}"
    else:
        print("(anonymous: skipping authed paths -- set WINSPOOL_SESSION_TOKEN or "
              "WINSPOOL_EMAIL/WINSPOOL_PASSWORD)\n")

    if a.cold:
        print(f"Idling {a.idle}s so Cloud Run can scale to zero...")
        time.sleep(a.idle)

    print(f"Target: {url}\n")
    print(f"{'path':32} {'1st TTFB':>9} {'med TTFB':>9} {'max TTFB':>9} {'med total':>10} {'wire KB':>8}  enc / cache")
    summary = {}
    for p in paths:
        try:
            first = measure(sess, url + p, headers)
            runs = [measure(sess, url + p, headers) for _ in range(a.n)]
        except requests.RequestException as e:
            print(f"{p:32} ERROR {e}")
            continue
        tt = [r["ttfb"] for r in runs]
        tot = [r["total"] for r in runs]
        flag = "" if first["status"] == 200 else f"  [HTTP {first['status']}]"
        print(f"{p:32} {first['ttfb']:8.0f}ms {st.median(tt):8.0f}ms {max(tt):8.0f}ms "
              f"{st.median(tot):9.0f}ms {first['wire']/1024:8.1f}  {first['enc']} / {first['cache']}{flag}")
        summary[p] = {"first_ttfb": round(first["ttfb"]), "med_ttfb": round(st.median(tt)),
                      "med_total": round(st.median(tot)), "wire_kb": round(first["wire"] / 1024, 1),
                      "enc": first["enc"], "status": first["status"]}

    print("\nReading it: a big '1st' vs 'med' TTFB gap = cold start / cache warm-up on the server."
          "\nHigh 'med TTFB' on '/' or an API = slow server-side work. enc=none on large JS/CSS/HTML = no compression.")
    append_history("http", url, {"cold": a.cold, "authed": bool(token), "paths": summary})


if __name__ == "__main__":
    main()
