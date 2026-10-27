# -*- coding: utf-8 -*-
"""Dos–responsmodellen för nattlig HRV.

Tre steg: ett renskuret sjukindex (vilopuls och andning mot egen baslinje), modellrader
(allt känt före natten) och den långsamma formen (EWMA av modellens tidigare missar).
Serien börjar vid första natten med HRV (eller vid `start`), och utvärderingsstarten T0
anpassas till hur mycket data som finns.

Konvention: en rad gäller dygn d (allt känt före natten) och utfallet y är
HRV natten d+1. Formen för datum d gäller alltså natten d+1.

Läser två tabeller:
  days(date, hrv_night, hrv_7d, rhr, respiration_avg, stress_avg, steps, ...)
  activities(date, type_key, duration_s, training_load, aerobic_te,
             anaerobic_te, start_local)
"""
import datetime as dt
import sqlite3

import numpy as np

MODELLVERSION = "1.0"
BASE = ['h7', 'stress', 'hour', 'smin', 'pstress', 'pload', 'load', 'sjuk']
TOTAL = ['h7', 'hour', 'smin', 'pload', 'load', 'sjuk']   # utan stress: lastens TOTALA effekt
HALVERINGSTIDER = [1, 2, 3, 5, 7, 10, 14, 21, 30]
STR = {'strength_training'}
GRUPPER = ['styrka', 'inomhuscykel', 'cykel', 'lopning', 'vandring', 'skidor', 'paddling', 'ovrigt']
MIN_RADER = 150          # färre modellrader än så: ingen modell


# ----------------------------------------------------------------- inläsning
DAY_COLS = ['date', 'hrv_night', 'hrv_7d', 'rhr', 'respiration_avg', 'skin_temp_dev_c',
            'stress_avg', 'sleep_seconds', 'sleep_score', 'deep_seconds', 'rem_seconds',
            'awake_seconds', 'steps', 'fetched_at']


def las(con: sqlite3.Connection):
    """(D, A): D[datum] = dict med dygnsvärden, A[datum] = lista av pass."""
    have = {r[1] for r in con.execute("PRAGMA table_info(days)")}
    cols = [c for c in DAY_COLS if c in have]
    D = {}
    for r in con.execute(f"select {','.join(cols)} from days order by date"):
        rec = dict.fromkeys(DAY_COLS)
        rec.update(zip(cols, r))
        D[r[0]] = rec
    A = {}
    for r in con.execute("""select date,type_key,duration_s,avg_hr,max_hr,aerobic_te,anaerobic_te,
                                   training_load,start_local
                            from activities order by date,start_local"""):
        A.setdefault(r[0], []).append(dict(zip(
            'date type dur hr hrmax ate ante load start'.split(), r)))
    return D, A


def grupp(tk):
    tk = (tk or '').lower()
    if 'strength' in tk: return 'styrka'
    if 'indoor_cycling' in tk or 'virtual_ride' in tk: return 'inomhuscykel'
    if 'cycling' in tk or 'biking' in tk or 'mountain' in tk: return 'cykel'
    if 'running' in tk or 'treadmill' in tk: return 'lopning'
    if 'hiking' in tk or 'walking' in tk: return 'vandring'
    if 'skate' in tk or 'ski' in tk: return 'skidor'
    if any(k in tk for k in ('paddl', 'kayak', 'canoe', 'rowing', 'sup')): return 'paddling'
    return 'ovrigt'


def iso(o: int) -> str:
    return dt.date.fromordinal(o).isoformat()


def ordn(s: str) -> int:
    return dt.date.fromisoformat(s).toordinal()


def forsta_hrv(D) -> str | None:
    ds = [d for d in sorted(D) if D[d]['hrv_night'] is not None]
    return ds[0] if ds else None


# ----------------------------------------------------------------- sjukindex (sjuk_ren.py)
def sjukindex(D, A, start: str) -> dict:
    """Renskuret fysiologiskt sjukindex: robust z av vilopuls- och andningsavvikelse mot
    60 dygns median bakåt, residualiserat mot aktivitetsminuter och last (idag + igår)."""
    dts = sorted(D)

    def trailing(key, win=60, minn=15):
        out = {}
        vals = [(ordn(d), D[d][key]) for d in dts if D[d][key] is not None]
        if not vals:
            return out
        arr = np.array(vals, float)
        for d in dts:
            n = ordn(d)
            m = (arr[:, 0] < n) & (arr[:, 0] >= n - win)
            if m.sum() >= minn:
                out[d] = np.median(arr[m, 1])
        return out

    base_rhr, base_res = trailing('rhr'), trailing('respiration_avg')
    rec = []
    for d in dts:
        r = D[d]
        if r['rhr'] is None or r['respiration_avg'] is None:
            continue
        if d not in base_rhr or d not in base_res:
            continue
        rec.append((d, r['rhr'] - base_rhr[d], r['respiration_avg'] - base_res[d]))
    if len(rec) < 30:
        return {}
    drhr = np.array([x[1] for x in rec]); dres = np.array([x[2] for x in rec])

    def z(v):
        spr = np.percentile(v, 84) - np.percentile(v, 16)
        return (v - np.median(v)) / spr * 2 if spr > 0 else np.zeros_like(v)

    idx = z(drhr) + z(dres)
    sidx = {x[0]: float(v) for x, v in zip(rec, idx)}

    mins, load = {}, {}
    for d, acts in A.items():
        for a in acts:
            mins[d] = mins.get(d, 0.) + (a['dur'] or 0) / 60
            load[d] = load.get(d, 0.) + (a['load'] or 0)

    def prv(d): return iso(ordn(d) - 1)

    dd = [d for d in sorted(sidx) if d >= start]
    if len(dd) < 30:
        return sidx
    ix = np.array([sidx[d] for d in dd])
    L = np.column_stack([np.ones(len(dd)),
                         [mins.get(d, 0.) for d in dd], [mins.get(prv(d), 0.) for d in dd],
                         [load.get(d, 0.) for d in dd], [load.get(prv(d), 0.) for d in dd]])
    bb, *_ = np.linalg.lstsq(L, ix, rcond=None)
    ren = dict(sidx)
    for d, v in zip(dd, ix - L @ bb):
        ren[d] = float(v)
    return ren


def fys_ep(idx, thr=2., minlen=2, tail=1, gap=3) -> set:
    ds = sorted(idx)
    o = {x: ordn(x) for x in ds}
    hi = [x for x in ds if idx[x] >= thr]
    out, i = set(), 0
    while i < len(hi):
        j = i
        while j + 1 < len(hi) and o[hi[j + 1]] - o[hi[j]] <= gap:
            j += 1
        if j - i + 1 >= minlen:
            for k in range(o[hi[i]] - tail, o[hi[j]] + tail + 1):
                out.add(iso(k))
        i = j + 1
    return out


# ----------------------------------------------------------------- modellrader (bygg_rows2.py)
def day_feats(A, d):
    acts = A.get(d, [])
    f = dict(ate=0., ante=0., load=0., smin=0., emin=0., hour=np.nan, n=len(acts))
    for g in GRUPPER:
        f['L_' + g] = 0.
        f['M_' + g] = 0.
    if not acts:
        return f
    f['ate'] = max((a['ate'] or 0) for a in acts)
    f['ante'] = max((a['ante'] or 0) for a in acts)
    f['load'] = sum((a['load'] or 0) for a in acts)
    f['smin'] = sum((a['dur'] or 0) / 60 for a in acts if a['type'] in STR)
    f['emin'] = sum((a['dur'] or 0) / 60 for a in acts if a['type'] not in STR)
    for a in acts:
        g = grupp(a['type'])
        f['L_' + g] += (a['load'] or 0)
        f['M_' + g] += (a['dur'] or 0) / 60
    hrs = []
    for a in acts:
        try:
            t = dt.datetime.fromisoformat(str(a['start']).replace(' ', 'T'))
            hrs.append(t.hour + t.minute / 60)
        except Exception:
            pass
    f['hour'] = max(hrs) if hrs else np.nan
    return f


def bygg_rader(D, A, start: str, sjuk: set) -> list[dict]:
    rows = []
    for d in sorted(D):
        if d < start:
            continue
        o = ordn(d)
        n1, p1 = iso(o + 1), iso(o - 1)
        if n1 not in D:
            continue
        y = D[n1]['hrv_night']
        if y is None:
            continue
        h7, s = D[d]['hrv_7d'], D[d]['stress_avg']
        if h7 is None or s is None or s < 0:
            continue
        f, fp = day_feats(A, d), day_feats(A, p1)
        ps = D[p1]['stress_avg'] if p1 in D and D[p1]['stress_avg'] is not None else None
        r = dict(
            date=d, ord=o, y=float(y),
            h7=float(h7), stress=float(s),
            ate=f['ate'], ante=f['ante'], load=f['load'], smin=f['smin'], emin=f['emin'],
            hour=f['hour'] if f['hour'] == f['hour'] else 12.0,
            nact=f['n'], pate=fp['ate'], pload=fp['load'],
            pstress=float(ps) if ps is not None and ps >= 0 else float(s),
            sjuk=1.0 if (d in sjuk or n1 in sjuk) else 0.0,
        )
        for g in GRUPPER:
            r['L_' + g] = f['L_' + g]
            r['M_' + g] = f['M_' + g]
        rows.append(r)
    return rows


# ----------------------------------------------------------------- långsam form (langsam.py)
class Kedja:
    """Expanderande fönster: varje dygns prognos bygger bara på strikt tidigare dygn."""

    def __init__(self, rows, t0=None):
        self.rows = rows
        self.d = {k: np.array([r[k] for r in rows], float) for k in rows[0] if k != 'date'}
        self.dates = [r['date'] for r in rows]
        self.y = self.d['y']
        self.N = len(self.y)
        self.o = self.d['ord'].astype(int)
        self.pos = {int(v): i for i, v in enumerate(self.o)}
        # T0 = 300 vid gott om data. Med mindre data krävs ändå tillräckligt många
        # träningsdygn för att 9 koefficienter ska bli stabila.
        self.t0 = t0 if t0 is not None else int(min(300, max(100, self.N // 3)))

    def design(self, keys, idx, extra=None):
        cols = [np.ones(len(idx))] + [self.d[k][idx] for k in keys]
        if extra is not None:
            cols += [e[idx] for e in extra]
        return np.column_stack(cols)

    def expanding(self, keys, extra=None):
        pred = np.full(self.N, np.nan)
        y = self.y
        for i in range(self.t0, self.N):
            tr = np.arange(i)
            X = self.design(keys, tr, extra)
            ok = np.isfinite(X).all(1)
            b, *_ = np.linalg.lstsq(X[ok], y[tr][ok], rcond=None)
            xi = self.design(keys, np.array([i]), extra)
            if np.isfinite(xi).all():
                pred[i] = (xi @ b)[0]
        return pred, np.isfinite(pred)

    def r2(self, pred, m):
        y = self.y
        return float(1 - np.sum((y[m] - pred[m]) ** 2) / np.sum((y[m] - y[m].mean()) ** 2))

    def mae(self, pred, m):
        return float(np.abs(self.y[m] - pred[m]).mean())

    def residualer(self, p0):
        T0, y = self.t0, self.y
        Xf = self.design(BASE, np.arange(self.N))
        bf, *_ = np.linalg.lstsq(Xf[:T0], y[:T0], rcond=None)
        res = np.empty(self.N)
        res[:T0] = y[:T0] - Xf[:T0] @ bf
        res[T0:] = y[T0:] - np.where(np.isfinite(p0[T0:]), p0[T0:], y[T0:])
        return res

    def ewma_feature(self, res, halflife, maxback=60):
        lam = 0.5 ** (1.0 / halflife)
        f = np.zeros(self.N)
        for i, v in enumerate(self.o):
            num = den = 0.0
            for k in range(1, maxback + 1):
                j = self.pos.get(int(v) - k)
                if j is None or j >= i:
                    continue
                w = lam ** k
                num += w * res[j]
                den += w
            f[i] = num / den if den > 0 else 0.0
        return f


def ols(X, y):
    """Koefficienter, standardfel och t (samma formel som langsam.py)."""
    ok = np.isfinite(X).all(1) & np.isfinite(y)
    X, y = X[ok], y[ok]
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    r = y - X @ b
    se = np.sqrt(np.diag(r @ r / (len(y) - X.shape[1]) * np.linalg.pinv(X.T @ X)))
    return b, se, int(len(y))


def acf(x, lag=1):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) <= lag + 2:
        return None
    x = x - x.mean()
    return float(np.sum(x[lag:] * x[:-lag]) / np.sum(x * x))


# ----------------------------------------------------------------- hela körningen
def kor(con: sqlite3.Connection, start: str | None = None, t0: int | None = None,
        progress=None) -> dict:
    """Kör kedjan. Returnerar en dict med serier (för dashboarden) och nyckeltal (för bidraget).
    Vid för lite data: {'ok': False, 'skal': ...}."""
    tick = progress or (lambda *_: None)
    D, A = las(con)
    first = forsta_hrv(D)
    if first is None:
        return {'ok': False, 'skal': 'Ingen nattlig HRV i databasen ännu.'}
    start = max(start or first, first)
    tick('Sjukindex', 0.05)
    sidx = sjukindex(D, A, start)
    sjuk = fys_ep(sidx) if sidx else set()
    rows = bygg_rader(D, A, start, sjuk)
    if len(rows) < MIN_RADER:
        return {'ok': False, 'n': len(rows),
                'skal': f'{len(rows)} användbara dygn med HRV, stress och HRV-veckosnitt. '
                        f'Modellen behöver minst {MIN_RADER}, helst 400 eller fler.'}

    K = Kedja(rows, t0)
    tick('Basmodellen', 0.10)
    p0, m0 = K.expanding(BASE)
    res = K.residualer(p0)

    scan, best = {}, None
    for i, hl in enumerate(HALVERINGSTIDER):
        tick(f'Långsam form, halveringstid {hl} d', 0.15 + 0.6 * i / len(HALVERINGSTIDER))
        f = K.ewma_feature(res, hl)
        p, m = K.expanding(BASE, extra=[f])
        v = K.r2(p, m)
        scan[hl] = v
        if best is None or v > best[1]:
            best = (hl, v, f, p, m)
    hl, r2_form, form, p_form, m_form = best

    tick('Jämförelser', 0.8)
    utan_h7 = [k for k in BASE if k != 'h7']
    p_nh7, m_nh7 = K.expanding(utan_h7)
    p_nh7f, m_nh7f = K.expanding(utan_h7, extra=[form])

    # Helsample-koefficienter i slutmodellen (direkt effekt) och utan stress (total effekt)
    N = K.N
    Xs = np.column_stack([np.ones(N)] + [K.d[k] for k in BASE] + [form])
    b, se, n_ols = ols(Xs, K.y)
    koef = {k: {'b': float(b[i + 1]), 'se': float(se[i + 1])} for i, k in enumerate(BASE + ['form'])}
    Xt = np.column_stack([np.ones(N)] + [K.d[k] for k in TOTAL] + [form])
    bt, set_, _ = ols(Xt, K.y)
    koef_total = {k: {'b': float(bt[i + 1]), 'se': float(set_[i + 1])} for i, k in enumerate(TOTAL + ['form'])}

    tick('Nyckeltal', 0.9)
    ext = extra_nyckeltal(K, D, A, form, res, sjuk, start)
    tick('Klart', 1.0)
    return {
        'ok': True, 'start': start, 't0': K.t0, 'n': N, 'dates': K.dates, 'rows': rows,
        'y': K.y, 'pred_base': p0, 'pred_form': p_form, 'res': res, 'form': form, 'hl': hl,
        'sjuk': sorted(sjuk),
        'r2': {'bas': K.r2(p0, m0), 'form': r2_form, 'utan_h7': K.r2(p_nh7, m_nh7),
               'utan_h7_form': K.r2(p_nh7f, m_nh7f)},
        'mae': {'bas': K.mae(p0, m0), 'form': K.mae(p_form, m_form)},
        'n_utv': int(m0.sum()),
        'scan': scan, 'koef': koef, 'koef_total': koef_total, 'n_ols': n_ols,
        **ext,
    }


def extra_nyckeltal(K, D, A, form, res, sjuk, start) -> dict:
    """Mått som gör det möjligt att jämföra personer: tunga pass, nedvarvning, formens egenskaper."""
    y, d, N = K.y, K.d, K.N
    out = {}

    # Formens egenskaper
    out['form_sd'] = float(np.std(form))
    out['form_p10'], out['form_p90'] = (float(np.percentile(form, q)) for q in (10, 90))
    out['form_acf1'] = acf(form, 1)
    out['res_acf1'] = acf(res[K.t0:], 1)
    lags = [1, 7, 14, 30, 60, 90]
    out['form_acf'] = {lag: acf(form, lag) for lag in lags}
    # Formens säsong (medel per månad, bara månader med minst 20 dygn)
    man = {}
    for dd, f in zip(K.dates, form):
        man.setdefault(int(dd[5:7]), []).append(f)
    out['form_manad'] = {m: float(np.mean(v)) for m, v in sorted(man.items()) if len(v) >= 20}

    # Tunga pass: dygn med last över egen 90:e percentil (bland passdygn), modellfritt.
    # Avvikelse = HRV natten efter − 7-dygnssnittet, jämfört med vilodygn.
    dev = y - d['h7']
    pass_load = d['load'][d['load'] > 0]
    tung = None
    if len(pass_load) >= 30:
        thr = float(np.percentile(pass_load, 90))
        heavy = np.where(d['load'] >= thr)[0]
        rest = np.where((d['load'] == 0) & (d['sjuk'] == 0))[0]
        n2 = [K.pos[int(K.o[i]) + 1] for i in heavy if int(K.o[i]) + 1 in K.pos]
        base_dev = float(dev[rest].mean()) if len(rest) else 0.0

        def ci(v):
            v = np.asarray(v, float)
            return float(v.mean() - base_dev), float(v.std(ddof=1) / np.sqrt(len(v))) if len(v) > 1 else None

        n1m, n1se = ci(dev[heavy])
        n2m, n2se = ci(dev[n2]) if len(n2) > 1 else (None, None)
        tung = {'grans_last': thr, 'n': int(len(heavy)), 'n_vila': int(len(rest)),
                'natt1': n1m, 'natt1_se': n1se, 'natt2': n2m, 'natt2_se': n2se,
                'medellast': float(d['load'][heavy].mean())}
    out['tungt_pass'] = tung

    # Dos–respons i last-klasser (för diagram): medelavvikelse natten efter per lastintervall
    klass = []
    for lo, hi_ in [(0, 0), (1, 50), (50, 100), (100, 150), (150, 200), (200, 300), (300, 10 ** 6)]:
        msk = (d['load'] == 0) if hi_ == 0 else ((d['load'] >= lo) & (d['load'] < hi_))
        msk = msk & (d['sjuk'] == 0)
        if msk.sum() >= 8:
            klass.append({'fran': lo, 'till': hi_, 'n': int(msk.sum()), 'avvikelse': float(dev[msk].mean())})
    out['lastklasser'] = klass

    # Nedvarvning: dagsstress mot eget 28-dygns-snitt ~ gårdagens last + dagens last
    sd = sorted(D)
    st_ = {x: D[x]['stress_avg'] for x in sd if D[x]['stress_avg'] is not None and D[x]['stress_avg'] >= 0}
    load_d = {x: sum((a['load'] or 0) for a in A.get(x, [])) for x in sd}
    Xr, yr = [], []
    for x in sd:
        if x < start or x not in st_:
            continue
        o = ordn(x)
        prev = [st_[iso(o - k)] for k in range(1, 29) if iso(o - k) in st_]
        if len(prev) < 14:
            continue
        Xr.append([1.0, load_d.get(iso(o - 1), 0.0), load_d.get(x, 0.0)])
        yr.append(st_[x] - np.mean(prev))
    if len(yr) >= 100:
        b, se, n = ols(np.array(Xr), np.array(yr))
        out['nedvarvning'] = {'b_igar': float(b[1]), 'se_igar': float(se[1]),
                              'b_idag': float(b[2]), 'se_idag': float(se[2]), 'n': n}
    else:
        out['nedvarvning'] = None

    # Beskrivning av personens data (inga datum, inga dygnsvärden)
    alla_last = np.array([load_d.get(x, 0.0) for x in sd if x >= start])
    typmin = {g: float(d['M_' + g].sum()) for g in GRUPPER}
    tot = sum(typmin.values()) or 1.0
    out['beskrivning'] = {
        'hrv_medel': float(y.mean()), 'hrv_sd': float(y.std(ddof=1)),
        'stress_medel': float(d['stress'].mean()), 'stress_sd': float(d['stress'].std(ddof=1)),
        'last_per_dygn': float(alla_last.mean()) if len(alla_last) else None,
        'andel_passdygn': float((d['load'] > 0).mean()),
        'andel_sjukdygn': float(d['sjuk'].mean()),
        'andel_styrkedygn': float((d['smin'] > 0).mean()),
        'typfordelning_min': {g: round(v / tot, 3) for g, v in typmin.items() if v > 0},
        'dygn_i_serien': int(K.o[-1] - K.o[0] + 1),
        'startar': start[:7],
        'sjukindex_finns': bool(sjuk) or any(D[x]['respiration_avg'] is not None for x in sd),
    }
    return out


if __name__ == '__main__':
    import sys
    import time
    path = sys.argv[1] if len(sys.argv) > 1 else 'data/hrv.db'
    start = sys.argv[2] if len(sys.argv) > 2 else None
    t = time.time()
    R = kor(sqlite3.connect(path), start=start, progress=lambda s, f: print(f'  [{f:4.0%}] {s}', flush=True))
    if not R['ok']:
        print(R['skal'])
        sys.exit(1)
    print(f"rader {R['n']}  {R['dates'][0]} -> {R['dates'][-1]}  T0 {R['t0']}  ({time.time() - t:.1f} s)")
    print(f"OOS R2 bas {R['r2']['bas']:.4f}  +form {R['r2']['form']:.4f}  "
          f"utan h7 {R['r2']['utan_h7']:.4f}  utan h7 +form {R['r2']['utan_h7_form']:.4f}")
    print(f"MAE bas {R['mae']['bas']:.2f}  +form {R['mae']['form']:.2f}   bästa halveringstid {R['hl']} d")
    print('scan', {k: round(v, 4) for k, v in R['scan'].items()})
    for k, v in R['koef'].items():
        print(f"  {k:8s} {v['b']:+.4f}  t {v['b'] / v['se']:+.2f}")
    print('total last', R['koef_total']['load'])
    print(f"form sd {R['form_sd']:.2f}  acf1 {R['form_acf1']:.3f}  res acf1 {R['res_acf1']:.3f}")
    print('tungt pass', R['tungt_pass'])
    print('nedvarvning', R['nedvarvning'])
