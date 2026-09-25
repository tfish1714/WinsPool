"""Real-browser page-load profile of the prod site (Playwright/Chromium).

Reports navigation timing, FCP/LCP, and a resource breakdown (slowest, largest,
per-host, API calls), with optional network/CPU throttling to mimic phones.

    python scripts/perf/perf_browser.py                   # desktop, login shell
    python scripts/perf/perf_browser.py --path /wins-pool --mobile    # needs auth env (see common.py)
    python scripts/perf/perf_browser.py --runs 3 --slow4g --cpu 4

Each run uses a fresh browser (cold HTTP cache) and the median run is reported;
--warm additionally reloads in the same context (cached assets).
"""
import argparse
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright

import json

from common import append_history, base_url, login, login_data

VITALS_JS = """() => new Promise(res => {
  const out = {};
  const nav = performance.getEntriesByType('navigation')[0];
  if (nav) Object.assign(out, {
    ttfb: nav.responseStart, dcl: nav.domContentLoadedEventEnd, load: nav.loadEventEnd});
  const fcp = performance.getEntriesByName('first-contentful-paint')[0];
  if (fcp) out.fcp = fcp.startTime;
  let lcp = null;
  new PerformanceObserver(l => { const e = l.getEntries(); lcp = e[e.length-1]; })
    .observe({type: 'largest-contentful-paint', buffered: true});
  const cls = [];
  new PerformanceObserver(l => l.getEntries().forEach(e => { if (!e.hadRecentInput) cls.push(e.value); }))
    .observe({type: 'layout-shift', buffered: true});
  setTimeout(() => {
    out.lcp = lcp && lcp.startTime;
    out.lcp_el = lcp && (lcp.element ? lcp.element.tagName + (lcp.element.id ? '#' + lcp.element.id : '') : lcp.url);
    out.cls = cls.reduce((a, b) => a + b, 0);
    out.resources = performance.getEntriesByType('resource').map(r => ({
      url: r.name, type: r.initiatorType, start: r.startTime, dur: r.duration,
      ttfb: r.responseStart - r.requestStart, enc: r.encodedBodySize, dec: r.decodedBodySize}));
    res(out);
  }, 1500);
})"""


def one_run(pw, url, path, token, mobile, slow4g, cpu, warm):
    b = pw.chromium.launch()
    ctx_args = dict(pw.devices["Pixel 7"]) if mobile else dict(viewport={"width": 1440, "height": 900})
    ctx = b.new_context(**ctx_args)
    if token:
        ctx.add_cookies([{"name": "session_token", "value": token, "url": url}])
    d = login_data()
    if d:  # mirror static/js/auth_service.js::saveCredentials so the SPA boots logged in
        kv = {"nfl_wins_my_player_id": d.get("playerId"), "nfl_wins_playerName": d.get("playerName"),
              "nfl_wins_nickName": d.get("nickName") or "", "nfl_wins_user_email": d.get("email"),
              "nfl_wins_role": d.get("role") or "user", "nfl_wins_token": d.get("token")}
        kv = {k: str(v) for k, v in kv.items() if v is not None}
        ctx.add_init_script(f"try{{for(const [k,v] of Object.entries({json.dumps(kv)}))localStorage.setItem(k,v)}}catch(e){{}}")
    page = ctx.new_page()
    cdp = ctx.new_cdp_session(page)
    if slow4g:
        cdp.send("Network.enable")
        cdp.send("Network.emulateNetworkConditions", {
            "offline": False, "latency": 150,
            "downloadThroughput": 1.6 * 1024 * 1024 / 8, "uploadThroughput": 750 * 1024 / 8})
    if cpu > 1:
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": cpu})
    failed, console_err = [], []
    page.on("requestfailed", lambda r: failed.append(r.url))
    page.on("console", lambda m: console_err.append(m.text) if m.type == "error" else None)
    page.goto(url + path, wait_until="networkidle", timeout=120000)
    v = page.evaluate(VITALS_JS)
    v["final_url"] = page.url
    v["failed"], v["console_errors"] = failed, console_err[:5]
    if warm:
        page.reload(wait_until="networkidle")
        w = page.evaluate(VITALS_JS)
        v["warm_load"], v["warm_fcp"] = w.get("load"), w.get("fcp")
    b.close()
    return v


def ms(x):
    return "   n/a" if x is None else f"{x:6.0f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--path", default="/")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--mobile", action="store_true")
    ap.add_argument("--slow4g", action="store_true")
    ap.add_argument("--cpu", type=int, default=1, help="CPU slowdown factor (4 ~ mid phone)")
    ap.add_argument("--warm", action="store_true")
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()
    url = base_url(a.url)
    a.path = "/" + a.path.lstrip("/")  # Git Bash rewrites a leading-slash arg; `--path wins-pool` is safe
    token = login(requests.Session(), url)

    with sync_playwright() as pw:
        runs = [one_run(pw, url, a.path, token, a.mobile, a.slow4g, a.cpu, a.warm) for _ in range(a.runs)]
    runs.sort(key=lambda r: r.get("load") or 0)
    m = runs[len(runs) // 2]  # median by load time
    host = urlparse(url).netloc

    prof = f"{'mobile' if a.mobile else 'desktop'}{' slow4g' if a.slow4g else ''}{f' cpu{a.cpu}x' if a.cpu > 1 else ''}"
    print(f"Target: {url}{a.path}  [{prof}, median of {a.runs}, cold cache]")
    if "auth-title" in str(m.get("lcp_el")):
        print("  WARNING: LCP is the login screen -- this run was not logged in (see common.py auth options)")
    print(f"\n  TTFB {ms(m.get('ttfb'))}ms | FCP {ms(m.get('fcp'))}ms | LCP {ms(m.get('lcp'))}ms "
          f"({m.get('lcp_el')}) | DCL {ms(m.get('dcl'))}ms | load {ms(m.get('load'))}ms | CLS {m.get('cls', 0):.3f}")
    print(f"  spread across runs (load): {[round(r.get('load') or 0) for r in runs]}")
    if a.warm:
        print(f"  warm reload: FCP {ms(m.get('warm_fcp'))}ms, load {ms(m.get('warm_load'))}ms")

    res = m["resources"]
    tot_enc = sum(r["enc"] for r in res) / 1024
    tot_dec = sum(r["dec"] for r in res) / 1024
    print(f"\n  {len(res)} requests, {tot_enc:.0f} KB over the wire ({tot_dec:.0f} KB decoded)")
    by_host = {}
    for r in res:
        d = by_host.setdefault(urlparse(r["url"]).netloc, [0, 0.0, 0.0])
        d[0] += 1
        d[1] += r["enc"] / 1024
        d[2] = max(d[2], r["start"] + r["dur"])
    for h, (n, kb, end) in sorted(by_host.items(), key=lambda x: -x[1][2]):
        print(f"    {'*' if h != host else ' '} {h:38} {n:3} req {kb:7.0f} KB  last finishes @ {end:5.0f}ms")
    print("    (* = third party)")

    def short(u):
        p = urlparse(u)
        return (p.netloc.replace(host, "") + p.path)[-58:]

    print("\n  Slowest (by finish time):")
    for r in sorted(res, key=lambda r: -(r["start"] + r["dur"]))[: a.top]:
        print(f"    ends {r['start'] + r['dur']:6.0f}ms  dur {r['dur']:6.0f}ms  {r['enc'] / 1024:7.1f}KB  {r['type']:7} {short(r['url'])}")
    print("\n  Largest:")
    for r in sorted(res, key=lambda r: -r["enc"])[: a.top]:
        print(f"    {r['enc'] / 1024:7.1f}KB (decoded {r['dec'] / 1024:7.1f})  {r['type']:7} {short(r['url'])}")
    api = [r for r in res if "/api/" in r["url"]]
    if api:
        print("\n  API calls:")
        for r in sorted(api, key=lambda r: -r["dur"]):
            print(f"    dur {r['dur']:6.0f}ms  server-wait {r['ttfb']:6.0f}ms  starts @ {r['start']:5.0f}ms  {short(r['url'])}")
    if m["failed"] or m["console_errors"]:
        print(f"\n  Failed requests: {m['failed'][:5]}\n  Console errors: {m['console_errors']}")

    append_history("browser", url, {
        "path": a.path, "profile": prof, "authed": bool(token),
        **{k: round(m[k]) for k in ("ttfb", "fcp", "lcp", "dcl", "load") if m.get(k) is not None},
        "requests": len(res), "wire_kb": round(tot_enc)})


if __name__ == "__main__":
    main()
