"""The replay behind turning on plan v2.2 P3.9 (26 Sep), kept so it can be re-run.

Same settled city-days, same venue ladders, the engine's own sigma; only the
centre differs: the engine's last pricing before the local day began
(coalesce(centre_c, forecast_max_c - bias_applied_c)) against the
station-corrected combination of the seven models' day-before runs, fitted
only on the 45 days before each scored day. Scored with the engine's own
compute_band_probabilities on the venue's ladder.

Inputs are two saved query outputs (json_agg), named in TR below:
  1. per verified city-day: city, date, unit, the engine centre, sigma, the
     ladder (band_lo, band_hi, open_low, open_high, settled_yes, band id);
  2. per city-day and model: the lead-1/2 Previous Runs max and the station
     max (derived_city_day_features.max_c).
Result on 26 Sep (582 city-days, 13-25 Sep): docs/PLAN_PROGRESS.md, P3.9.
"""
import json, re, math, random, sys, datetime as dt
sys.path.insert(0, "scripts")
import station_correction as sc
from probability_engine import compute_band_probabilities
TR = "/root/.claude/projects/-home-user-arbdesk4/ad76a6a8-9533-5648-a613-cc75d5efd099/tool-results/"
def load(name):
    raw = open(TR + name).read()
    return json.loads(re.search(r'\[\{"json_agg":(\[.*?\])\}\]', raw.replace('\\"', '"')).group(1))
days = load("mcp-Supabase-execute_sql-1790447549912.txt")
pairs = [(c, d, s, int(l), float(f), float(y)) for c, d, s, l, f, y in load("mcp-Supabase-execute_sql-1790445100012.txt")]
by_day = {}
for p in pairs:
    by_day.setdefault((p[0], p[1]), []).append(p)
fits = {}
def table_for(d):
    if d not in fits:
        start = (dt.date.fromisoformat(d) - dt.timedelta(days=sc.WINDOW_DAYS)).isoformat()
        fits[d] = sc.fit([p for p in pairs if start <= p[1] < d])
    return fits[d]
rows = []
for city, d, unit, centre, sigma, ladder, *_rest in days:
    if centre is None or sigma is None: continue
    bands = [{"band_id": b[5], "band_lo": float(b[0]) if b[0] is not None else None,
              "band_hi": float(b[1]) if b[1] is not None else None, "open_low": b[2], "open_high": b[3]} for b in ladder]
    win = [b[5] for b in ladder if b[4]]
    if len(win) != 1: continue
    fcs = {s: f for c_, d_, s, l, f, y in by_day.get((city, d), []) if l == 1}
    ys = [y for c_, d_, s, l, f, y in by_day.get((city, d), [])]
    if not ys: continue
    t = table_for(d)
    comb = sc.combine(fcs, t, city, 1)
    if comb is None: continue
    out = {}
    for name, c in (("engine", float(centre)), ("corrected", comb[0])):
        probs = dict(compute_band_probabilities(c, float(sigma), unit, bands))
        top = max(probs, key=probs.get)
        pw = max(probs.get(win[0], 0.0), 1e-6)
        out[name] = dict(hit=top == win[0], ll=-math.log(pw), brier=sum((p - (1 if b == win[0] else 0)) ** 2 for b, p in probs.items()),
                         err=abs(c - ys[0]), pwin=pw)
    rows.append((d, unit, out))
n = len(rows)
dates = sorted({r[0] for r in rows})
print(f"city-days {n}, dates {dates[0]}..{dates[-1]} ({len(dates)}), F cities {sum(1 for r in rows if r[1]=='F')}")
for k in ("engine", "corrected"):
    m = lambda f: sum(f(r[2][k]) for r in rows) / n
    print(f"{k:10s} hit {m(lambda x: x['hit']):.3f}  log loss {m(lambda x: x['ll']):.3f}  brier {m(lambda x: x['brier']):.3f}  centre MAE {m(lambda x: x['err']):.3f} C  p(winner) {m(lambda x: x['pwin']):.3f}")
for unit in ("C", "F"):
    sub = [r for r in rows if r[1] == unit]
    if sub:
        print(f"  {unit}: n={len(sub)} hit engine {sum(r[2]['engine']['hit'] for r in sub)/len(sub):.3f} corrected {sum(r[2]['corrected']['hit'] for r in sub)/len(sub):.3f}")
rng = random.Random(11); bd = {}
for d, u, o in rows: bd.setdefault(d, []).append(o)
dh, dl = [], []
for _ in range(4000):
    pick = [dates[rng.randrange(len(dates))] for _ in dates]
    xs = [o for d in pick for o in bd[d]]
    dh.append(sum(o['corrected']['hit'] - o['engine']['hit'] for o in xs) / len(xs))
    dl.append(sum(o['engine']['ll'] - o['corrected']['ll'] for o in xs) / len(xs))
dh.sort(); dl.sort()
print(f"hit gain corrected - engine {sum(dh)/len(dh):+.3f} 95% [{dh[100]:+.3f}, {dh[3899]:+.3f}]")
print(f"log loss gain engine - corrected {sum(dl)/len(dl):+.3f} 95% [{dl[100]:+.3f}, {dl[3899]:+.3f}]")
