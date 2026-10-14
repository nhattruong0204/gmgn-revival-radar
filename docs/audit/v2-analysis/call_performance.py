import json
import sqlite3
import statistics as st
import sys
import time

D = sys.argv[1]
d = sqlite3.connect(f"file:{D}?mode=ro", uri=True)
d.row_factory = sqlite3.Row
NOW = d.execute("select max(finished) from scan_runs").fetchone()[0]
rows = []
ALERT_Q = (
    "select a.*,s.market_cap mc0,s.price p0,s.stage,s.revision rev "
    "from alerts a left join alert_signals s on s.alert_id=a.id "
    'where delivery_status="sent" order by id'
)
for a in d.execute(ALERT_Q):
    snaps = d.execute(
        "select timestamp,market_cap,price from token_snapshots "
        "where chain=? and contract_address=? and timestamp>=? order by timestamp",
        (a["chain"], a["contract_address"], a["timestamp"]),
    ).fetchall()
    sym = d.execute(
        "select json_extract(value,'$.token.symbol') from state where key=?",
        (f"telegram:alert:{a['id']}",),
    ).fetchone()
    sym = sym[0] if sym else None
    if not sym:
        r = d.execute(
            "select symbol from token_snapshots where chain=? and contract_address=? limit 1",
            (a["chain"], a["contract_address"]),
        ).fetchone()
        sym = r[0] if r else "?"
    mc0 = a["mc0"]
    if not snaps or not mc0:
        rows.append(dict(id=a["id"], sym=sym, t=a["timestamp"], mc0=mc0, n=len(snaps)))
        continue
    mcs = [s["market_cap"] for s in snaps if s["market_cap"]]
    last = snaps[-1]
    rows.append(
        dict(
            id=a["id"],
            sym=sym,
            ca=a["contract_address"][:6],
            t=a["timestamp"],
            stage=a["stage"],
            score=a["score"],
            rev=a["rev"],
            mc0=mc0,
            mclast=last["market_cap"],
            tlast=last["timestamp"],
            chg=(last["market_cap"] / mc0 - 1) * 100 if last["market_cap"] else None,
            mfe=(max(mcs) / mc0 - 1) * 100,
            mae=(min(mcs) / mc0 - 1) * 100,
            n=len(snaps),
        )
    )
print("scanner now", time.strftime("%Y-%m-%d %H:%M", time.gmtime(NOW)))
print(
    f"{'id':>3} {'sym':9} {'alert UTC':16} {'stg':5} sc rev {'MC@call':>8} {'MC last':>8} "
    f"{'lastobs age':>11} {'chg%':>7} {'MFE%':>7} {'MAE%':>7} nsnap"
)
for r in rows:
    when = time.strftime("%m-%d %H:%M", time.gmtime(r["t"]))
    if "chg" not in r:
        print(
            f"{r['id']:>3} {r['sym'] or '?':9} {when:16} "
            f"no alert-time MC or no later snapshots (n={r['n']})"
        )
        continue
    print(
        f"{r['id']:>3} {r['sym'][:9]:9} {when:16} {(r['stage'] or '')[:5]:5} "
        f"{r['score']} {r['rev']} {r['mc0'] / 1e6:7.2f}M {r['mclast'] / 1e6:7.2f}M "
        f"{(NOW - r['tlast']) / 3600:9.1f}h {r['chg']:7.1f} {r['mfe']:7.1f} "
        f"{r['mae']:7.1f} {r['n']}"
    )
v = [r for r in rows if r.get("chg") is not None]
print("\nN usable", len(v), "of", len(rows))
for lab, sub in [
    ("all", v),
    ("last7d", [r for r in v if r["t"] >= NOW - 7 * 86400]),
    ("rev>=9 (thr40)", [r for r in v if (r["rev"] or 0) >= 9]),
]:
    c = [r["chg"] for r in sub]
    if not c:
        continue
    print(
        f"{lab}: n={len(c)} pos={sum(x > 0 for x in c)} neg={sum(x < 0 for x in c)} "
        f"median={st.median(c):.1f}% mean={st.mean(c):.1f}% "
        f"medMFE={st.median(r['mfe'] for r in sub):.1f}% "
        f"medMAE={st.median(r['mae'] for r in sub):.1f}% "
        f"MFE>=25%:{sum(r['mfe'] >= 25 for r in sub)} "
        f"MFE>=50%:{sum(r['mfe'] >= 50 for r in sub)}"
    )
with open(D + ".perf.json", "w") as fh:
    json.dump(rows, fh)
