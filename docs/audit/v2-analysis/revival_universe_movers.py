import bisect
import json
import sqlite3
import statistics as st
import sys
from collections import Counter, defaultdict

D = sys.argv[1]
d = sqlite3.connect(f"file:{D}?mode=ro", uri=True)
NOW = d.execute("select max(finished) from scan_runs").fetchone()[0]
snap = defaultdict(list)
SNAP_Q = (
    "select chain,contract_address,timestamp,market_cap,drawdown_from_ath,"
    "token_age_seconds,liquidity from token_snapshots "
    'where market_cap>0 and timestamp>=strftime("%s","2026-10-09 15:00") order by timestamp'
)
for c, a, t, mc, dd, age, liq in d.execute(SNAP_Q):
    snap[(c, a)].append((t, mc, dd, age, liq))
ev = defaultdict(list)
EV_Q = (
    "select chain,contract_address,timestamp,symbol,score,status,eligible,rejection_reasons "
    'from evaluations where timestamp>=strftime("%s","2026-10-09 15:00") order by timestamp'
)
for c, a, t, sym, score, status, elig, rej in d.execute(EV_Q):
    ev[(c, a)].append((t, sym, score, status, elig, json.loads(rej)))
alerted = {(r[0], r[1]) for r in d.execute("select chain,contract_address from alerts")}
uni = []
reasons = Counter()
for key, ss in snap.items():
    t0, mc0, dd, age, liq = ss[0]
    if not (mc0 >= 300e3 and (dd or 0) >= 0.4 and (age or 0) >= 3 * 86400):
        continue
    ts = [s[0] for s in ss]
    j = bisect.bisect_right(ts, t0 + 86400)
    seg = [s[1] for s in ss[:j]]
    if ts[-1] < t0 + 6 * 3600:
        continue
    mfe = (max(seg) / mc0 - 1) * 100
    mae = (min(seg) / mc0 - 1) * 100
    k = seg.index(max(seg))
    tpk = ts[k]
    evs = [e for e in ev.get(key, []) if e[0] <= tpk]
    r = Counter()
    for e in evs:
        for x in e[5]:
            r[x.split(":")[0].split("(")[0].strip()[:40]] += 1
    uni.append(
        dict(
            key=key,
            sym=(evs or ev.get(key, [(0, "?")]))[0][1],
            mc0=mc0,
            dd=dd,
            mfe=mfe,
            mae=mae,
            hrs=(tpk - t0) / 3600,
            maxscore=max([e[2] for e in evs], default=None),
            elig=any(e[4] for e in evs),
            alerted=key in alerted,
            reasons=r.most_common(4),
        )
    )
print(
    "revival-universe tokens (mc>=300k, dd>=40%, age>=3d, >=6h tracked):",
    len(uni),
)
for lab, f in [
    ("alerted", lambda u: u["alerted"]),
    ("not alerted", lambda u: not u["alerted"]),
]:
    s = [u for u in uni if f(u)]
    if s:
        print(
            f"{lab}: n={len(s)} median 24h-MFE={st.median(u['mfe'] for u in s):.1f}% "
            f"MFE>=50%: {sum(u['mfe'] >= 50 for u in s)} "
            f"MFE>=100%: {sum(u['mfe'] >= 100 for u in s)} "
            f"median MAE={st.median(u['mae'] for u in s):.1f}%"
        )
uni.sort(key=lambda u: -u["mfe"])
print("\nTop revival-universe movers (24h from first observation):")
for u in uni[:20]:
    print(
        f"{u['sym'][:10]:10} {u['key'][1][:6]} MC0 {u['mc0'] / 1e6:5.2f}M "
        f"dd {u['dd'] * 100:3.0f}% MFE {u['mfe']:6.0f}% in {u['hrs']:4.1f}h "
        f"maxscore {u['maxscore']} elig {u['elig']!s:5} alerted {u['alerted']!s:5} "
        f"{u['reasons']}"
    )
allr = Counter()
for u in uni:
    if u["mfe"] >= 50 and not u["alerted"]:
        for k, _v in u["reasons"]:
            allr[k] += 1
print(
    "\nRejection reasons among non-alerted >=50% movers (token count):",
    allr.most_common(12),
)
