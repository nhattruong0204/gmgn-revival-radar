import sqlite3,bisect,statistics as st,sys,json
from collections import defaultdict
D=sys.argv[1]
d=sqlite3.connect(f'file:{D}?mode=ro',uri=True)
NOW=d.execute('select max(finished) from scan_runs').fetchone()[0]
snap=defaultdict(lambda:([],[]))
for c,a,t,mc in d.execute('select chain,contract_address,timestamp,market_cap from token_snapshots where market_cap>0 order by timestamp'):
    s=snap[(c,a)]; s[0].append(t); s[1].append(mc)
alerted={(r[0],r[1]) for r in d.execute('select chain,contract_address from alerts')}
alert_times=defaultdict(list)
for c,a,t in d.execute('select chain,contract_address,timestamp from alerts'): alert_times[(c,a)].append(t)
def fwd(key,t,h):
    ts,mc=snap.get(key,([],[]))
    i=bisect.bisect_left(ts,t-60)
    if i>=len(ts) or abs(ts[i]-t)>600: return None,'no_base'
    base=mc[i]
    if t+h*3600>NOW: return None,'pending'
    j=bisect.bisect_left(ts,t+h*3600)
    if j>=len(ts) or ts[j]>t+h*3600+3600: return None,'missing'
    seg=mc[i:j+1]
    return ((mc[j]/base-1)*100, (max(seg)/base-1)*100, (min(seg)/base-1)*100),'ok'
# one sample per token per 6h bucket per cohort: first evaluation in bucket
cohorts=defaultdict(list); seen=set(); cov=defaultdict(lambda:defaultdict(int))
q='''select chain,contract_address,timestamp,score,status,eligible from evaluations where timestamp>=strftime('%s','2026-10-09 15:00') order by timestamp'''
for c,a,t,score,status,elig in d.execute(q):
    key=(c,a)
    if elig and score>=60: coh='E: eligible score>=60'
    elif elig and score>=40: coh='B: eligible score 40-59 (alert-equivalent)'
    elif elig: coh='C: eligible score<40'
    else: coh='D: ineligible (failed core filters)'
    if any(abs(t-x)<300 for x in alert_times.get(key,[])): coh='A: actual alert'
    b=(coh,key,int(t//21600))
    if b in seen: continue
    seen.add(b)
    for h in (6,24):
        r,stt=fwd(key,t,h); cov[(coh,h)][stt]+=1
        if r: cohorts[(coh,h)].append(r)
for (coh,h) in sorted(cohorts):
    v=cohorts[(coh,h)]; c=[x[0] for x in v]
    cv=dict(cov[(coh,h)])
    print(f"{coh:45} +{h:>2}h n={len(c):5} cov={cv} median={st.median(c):6.1f}% mean={st.mean(c):6.1f}% pos={sum(x>0 for x in c)/len(c)*100:4.0f}% medMFE={st.median(x[1] for x in v):5.1f}% medMAE={st.median(x[2] for x in v):6.1f}% MFE>=50%={sum(x[1]>=50 for x in v)/len(v)*100:4.1f}% MAE<=-30%={sum(x[2]<=-30 for x in v)/len(v)*100:4.1f}%")
