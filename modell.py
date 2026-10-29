# -*- coding: utf-8 -*-
"""Dos–responsmodellen för nattlig HRV.

Tre steg: ett renskuret sjukindex (vilopuls och andning mot egen baslinje), modellrader
(allt känt före natten) och den långsamma formen (EWMA av modellens tidigare missar).
Serien börjar vid första natten med HRV (eller vid `start`), och utvärderingsstarten T0
anpassas till hur mycket data som finns.

Konvention: en rad gäller dygn d (allt känt före natten) och utfallet y är
HRV natten d+1. Formen för datum d gäller alltså natten d+1.

Vilopulsvägen (punkt 10b): för klockor utan HRV-status är utfallet i stället vilopulsen natten d+1
(slag/min, högre = sämre) och nivåtermen vilopulsens 7-dygnsmedel. Samma termer i övrigt, men
sjukflaggan bygger bara på andningen. Se `kor(utfall=...)`.

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
# Vilopulsvägen: r7 = vilopulsens 7-dygnsmedel (slag/min) i stället för HRV-veckosnittet, i övrigt samma termer
BASE_VP = ['r7', 'stress', 'hour', 'smin', 'pstress', 'pload', 'load', 'sjuk']
TOTAL_VP = ['r7', 'hour', 'smin', 'pload', 'load', 'sjuk']
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


def forsta_vilopuls(D) -> str | None:
    ds = [d for d in sorted(D) if D[d]['rhr'] is not None]
    return ds[0] if ds else None


def vilopuls_7d(D, minn=4) -> dict:
    """Vilopulsens medel över nätterna d−6 … d (natten mot d räknas med, som Garmins HRV-veckosnitt).
    Minst `minn` nätter med värde, annars saknas nivån det dygnet."""
    v = {ordn(d): D[d]['rhr'] for d in D if D[d]['rhr'] is not None}
    out = {}
    for d in D:
        o = ordn(d)
        x = [v[o - k] for k in range(7) if o - k in v]
        if len(x) >= minn:
            out[d] = float(np.mean(x))
    return out


# ----------------------------------------------------------------- sjukindex (sjuk_ren.py)
def sjukindex(D, A, start: str, somn: bool = False, bara_andning: bool = False) -> dict:
    """Renskuret fysiologiskt sjukindex: robust z av vilopuls- och andningsavvikelse mot
    60 dygns median bakåt, residualiserat mot aktivitetsminuter och last (idag + igår).

    somn=True (variant 2, punkt 5): residualiseras även mot nattens sömnlängd (natten som slutar på
    morgonen, samma natt som vilopulsen) och mot en indikator för pass utan last (t.ex. fridykning,
    andningsövningar), så att en störd natt (barn, sen kväll) eller en träningsdag utan last inte tas
    för sjukdom. Basmodellen använder somn=False.

    bara_andning=True (vilopulsvägen, punkt 10b): bara andningens avvikelse. När vilopulsen är utfallet
    kan den inte också ligga i en prediktor, för då förklarar natten sig själv: en hög vilopuls natten
    d+1 skulle ge sjukflagga och flaggan skulle "förutsäga" samma höga vilopuls. Andningen mäts som
    en egen storhet och flaggan kräver minst två dygn över tröskeln, så den fångar sjukperioder men
    inte enstaka nätters vilopulssvängningar. Saknar klockan andning blir det ingen sjukflagga alls."""
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
        if bara_andning:
            if r['respiration_avg'] is not None and d in base_res:
                rec.append((d, 0.0, r['respiration_avg'] - base_res[d]))
            continue
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

    idx = z(dres) if bara_andning else z(drhr) + z(dres)
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
    if somn:
        sl = np.array([D[d]['sleep_seconds'] / 3600 if D[d]['sleep_seconds'] else np.nan for d in dd])
        sl = np.where(np.isfinite(sl), sl, np.nanmedian(sl) if np.isfinite(sl).any() else 7.5)
        utan_last = [1.0 if (d in A and load.get(d, 0.) == 0) else 0.0 for d in dd]
        L = np.column_stack([L, sl, utan_last])
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
LAGINT = 0.3   # last per minut under detta = lågintensivt pass (vandring, fridykning, yoga). Medianpasset ~2,0.


def day_feats(A, d):
    acts = A.get(d, [])
    f = dict(ate=0., ante=0., load=0., smin=0., emin=0., lmin=0., hour=np.nan, n=len(acts))
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
    # Minuter i pass som klockan knappt ger last (punkt 6). Pass utan längd kan inte bedömas och räknas inte.
    f['lmin'] = sum(a['dur'] / 60 for a in acts if a['type'] not in STR and a['dur']
                    and (a['load'] or 0) / (a['dur'] / 60) < LAGINT)
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


def bygg_rader(D, A, start: str, sjuk: set, utfall: str = 'hrv') -> list[dict]:
    """utfall='vilopuls': y = vilopulsen natten d+1 och nivåtermen r7 = vilopulsens 7-dygnsmedel
    (i stället för y = HRV och h7). Alla andra kolumner är desamma."""
    vp = utfall == 'vilopuls'
    r7 = vilopuls_7d(D) if vp else None
    niva_namn = 'r7' if vp else 'h7'
    rows = []
    for d in sorted(D):
        if d < start:
            continue
        o = ordn(d)
        n1, p1 = iso(o + 1), iso(o - 1)
        if n1 not in D:
            continue
        y = D[n1]['rhr'] if vp else D[n1]['hrv_night']
        if y is None:
            continue
        h7, s = (r7.get(d) if vp else D[d]['hrv_7d']), D[d]['stress_avg']
        if h7 is None or s is None or s < 0:
            continue
        f, fp = day_feats(A, d), day_feats(A, p1)
        ps = D[p1]['stress_avg'] if p1 in D and D[p1]['stress_avg'] is not None else None
        r = dict(
            date=d, ord=o, y=float(y),
            **{niva_namn: float(h7)}, stress=float(s),
            ate=f['ate'], ante=f['ante'], load=f['load'], smin=f['smin'], emin=f['emin'], lmin=f['lmin'],
            hour=f['hour'] if f['hour'] == f['hour'] else 12.0,
            nact=f['n'], pate=fp['ate'], pload=fp['load'],
            pstress=float(ps) if ps is not None and ps >= 0 else float(s),
            sjuk=1.0 if (d in sjuk or n1 in sjuk) else 0.0,
            # Natten som slutade på morgonen dygn d: känd före utfallsnatten. Används bara i sömnvarianten.
            somn_h=D[d]['sleep_seconds'] / 3600 if D[d]['sleep_seconds'] else np.nan,
            vaken_min=D[d]['awake_seconds'] / 60 if D[d]['awake_seconds'] is not None else np.nan,
        )
        for g in GRUPPER:
            r['L_' + g] = f['L_' + g]
            r['M_' + g] = f['M_' + g]
        rows.append(r)
    return rows


# ----------------------------------------------------------------- långsam form (langsam.py)
class Kedja:
    """Expanderande fönster: varje dygns prognos bygger bara på strikt tidigare dygn."""

    def __init__(self, rows, t0=None, spec=BASE):
        self.rows = rows
        self.spec = spec          # BASE för HRV, BASE_VP för vilopulsvägen
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
        Xf = self.design(self.spec, np.arange(self.N))
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


def tillracklig(X, y, namn) -> dict:
    """Tillräcklig statistik för den linjära modellen y ~ X: n, X'X, X'y, y'y (X med konstant först).

    Med dem kan en linjär blandad modell över flera personer skattas exakt (slumpmässiga effekter
    på alla koefficienter, egen restvarians per person) utan att några dygnsvärden lämnar datorn.
    'std' är samma sak på standardiserade variabler: varje förklarande variabel centrerad och delad
    med personens egen sd, utfallet likaså. Då blir koefficienterna jämförbara mellan klockor med
    olika enheter (Garmin mot Oura). Kolumner utan spridning (t.ex. ingen styrka alls) får sd 0 och
    står kvar som nollor; samla.py hanterar dem som saknad information för den personen."""
    ok = np.isfinite(X).all(1) & np.isfinite(y)
    X, y = X[ok], y[ok]
    n = len(y)
    mx, sx = X[:, 1:].mean(0), X[:, 1:].std(0, ddof=1)
    my, sy = y.mean(), y.std(ddof=1)
    Z = np.column_stack([np.ones(n), np.where(sx > 0, (X[:, 1:] - mx) / np.where(sx > 0, sx, 1), 0.0)])
    zy = (y - my) / sy
    stat = lambda A, v: {'XtX': (A.T @ A).tolist(), 'Xty': (A.T @ v).tolist(), 'yty': float(v @ v)}
    return {'namn': ['konst'] + list(namn), 'n': int(n),
            'ra': stat(X, y),
            'std': {**stat(Z, zy), 'medel_x': mx.tolist(), 'sd_x': sx.tolist(),
                    'medel_y': float(my), 'sd_y': float(sy)}}


def acf(x, lag=1):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) <= lag + 2:
        return None
    x = x - x.mean()
    return float(np.sum(x[lag:] * x[:-lag]) / np.sum(x * x))


# ----------------------------------------------------------------- lång lastlagg (Banister, tröskel)
FITNESS_HL = [14, 21, 30, 42, 60, 90]   # halveringstider (dygn) som skannas för det långa lastminnet
TROTTHET_HL = [2, 3, 5, 7, 10]          # och för det korta


def expanding_snabb(X, y, t0):
    """Samma prognos som Kedja.expanding (varje dygn skattas på strikt tidigare dygn), men med
    stegvis uppdaterade summor X'X och X'y i stället för en ny minsta kvadrat-lösning från början.
    Rader med saknade värden hoppas över i skattningen och får ingen prognos, som i originalet."""
    N, p = X.shape
    ok = np.isfinite(X).all(1) & np.isfinite(y)
    pred = np.full(N, np.nan)
    A, c = np.zeros((p, p)), np.zeros(p)
    for i in range(N):
        if i >= t0 and ok[i]:
            try:
                b = np.linalg.solve(A, c)
            except np.linalg.LinAlgError:
                b = np.linalg.lstsq(A, c, rcond=None)[0]
            pred[i] = X[i] @ b
        if ok[i]:
            A += np.outer(X[i], X[i])
            c += X[i] * y[i]
    return pred, np.isfinite(pred)


def lastserie(A) -> dict:
    """Daglig träningslast för varje kalenderdygn från första passet (dygn utan pass = 0)."""
    last = {}
    for d, acts in A.items():
        last[ordn(d)] = sum((a['load'] or 0) for a in acts)
    if not last:
        return {}
    o0, o1 = min(last), max(last)
    return {o: last.get(o, 0.0) for o in range(o0, o1 + 1)}


def lastminne(serie: dict, ords, halflife: float) -> np.ndarray:
    """Glidande medel (Banister/Coggan) av den dagliga lasten till och med dygn d:
    F_d = λ·F_{d−1} + (1−λ)·last_d, λ = 0,5^(1/halveringstid). Allt är känt före natten efter d."""
    if not serie:
        return np.zeros(len(ords))
    lam = 0.5 ** (1.0 / halflife)
    f, F = 0.0, {}
    for o in range(min(serie), max(max(serie), int(max(ords))) + 1):
        f = lam * f + (1 - lam) * serie.get(o, 0.0)
        F[o] = f
    return np.array([F.get(int(o), 0.0) for o in ords])


def lang_last(K, A, form) -> dict:
    """Punkt 4: bär träningslasten ett långt minne som den långsamma formen inte redan fångar?

    Jämför ut ur stickprovet (samma T0 som huvudmodellen) mot basmodellen och mot basmodellen med form:
      - Banister: + fitness (lång halveringstid) + trötthet (kort), alla kombinationer skannas
      - tröskel: + max(last − knut, 0), last över knuten får en egen lutning, knutar ur egna percentiler
    BASE-specen ändras inte; det här är jämförelser vid sidan av."""
    y, N, t0 = K.y, K.N, K.t0
    Xb = K.design(K.spec, np.arange(N))
    pb, mb = expanding_snabb(Xb, y, t0)
    pf, mf = expanding_snabb(np.column_stack([Xb, form]), y, t0)
    r2_bas, r2_form = K.r2(pb, mb), K.r2(pf, mf)
    serie = lastserie(A)
    fit = {h: lastminne(serie, K.o, h) for h in FITNESS_HL}
    tro = {h: lastminne(serie, K.o, h) for h in TROTTHET_HL}

    skanning, bast = {}, None
    for hf in FITNESS_HL:
        for ht in TROTTHET_HL:
            p, m = expanding_snabb(np.column_stack([Xb, fit[hf], tro[ht]]), y, t0)
            v = K.r2(p, m)
            skanning[f"{hf}/{ht}"] = v
            if bast is None or v > bast[2]:
                bast = (hf, ht, v)
    hf, ht, r2_ban = bast
    p, m = expanding_snabb(np.column_stack([Xb, form, fit[hf], tro[ht]]), y, t0)
    r2_ban_form = K.r2(p, m)
    b, se, _ = ols(np.column_stack([Xb, fit[hf], tro[ht]]), y)
    bf, sef, _ = ols(np.column_stack([Xb, form, fit[hf], tro[ht]]), y)
    banister = {
        "fitness_hl": hf, "trotthet_hl": ht, "r2": r2_ban, "r2_med_form": r2_ban_form,
        "vinst_mot_bas": r2_ban - r2_bas, "vinst_mot_form": r2_ban_form - r2_form,
        "fitness": {"b": float(b[-2]), "se": float(se[-2])}, "trotthet": {"b": float(b[-1]), "se": float(se[-1])},
        "fitness_med_form": {"b": float(bf[-2]), "se": float(sef[-2])},
        "trotthet_med_form": {"b": float(bf[-1]), "se": float(sef[-1])},
        "skanning": skanning,
        "fitness_sd": float(np.std(fit[hf])), "trotthet_sd": float(np.std(tro[ht])),
    }

    last = K.d['load']
    pos = last[last > 0]
    knutar = sorted({round(float(np.percentile(pos, q))) for q in (50, 75, 90)}) if len(pos) >= 30 else []
    tskan, tbast = {}, None
    for k in knutar:
        hinge = np.maximum(last - k, 0.0)
        if (hinge > 0).sum() < 15:
            continue
        p, m = expanding_snabb(np.column_stack([Xb, form, hinge]), y, t0)
        v = K.r2(p, m)
        tskan[str(k)] = v
        if tbast is None or v > tbast[1]:
            tbast = (k, v)
    troskel = None
    if tbast:
        k, v = tbast
        hinge = np.maximum(last - k, 0.0)
        b, se, _ = ols(np.column_stack([Xb, form, hinge]), y)
        troskel = {"knut": k, "r2_med_form": v, "vinst_mot_form": v - r2_form,
                   "under_knut": {"b": float(b[K.spec.index('load') + 1]), "se": float(se[K.spec.index('load') + 1])},
                   "over_knut_extra": {"b": float(b[-1]), "se": float(se[-1])},
                   "n_over": int((hinge > 0).sum()), "skanning": tskan}
    return {"r2_bas": r2_bas, "r2_form": r2_form, "banister": banister, "troskel": troskel}


# ----------------------------------------------------------------- sömn (punkt 5)
def somn_variant(K, D, A, form, start) -> dict:
    """Punkt 5, som variant vid sidan av basmodellen (som inte ändras):
      - sjukindex 2: residualiserat även mot nattens sömnlängd och pass utan last
      - föregående natts sömnlängd (h) och vakna minuter som prediktorer (natten som slutade på
        morgonen dygn d, känd före utfallsnatten)
    Allt jämförs ut ur stickprovet på samma dygn. Saknad sömn ersätts med medianen från de första
    T0 dygnen, så att ingen framtid läcker in och alla varianter utvärderas på samma rader."""
    y, N, t0 = K.y, K.N, K.t0
    sidx2 = sjukindex(D, A, start, somn=True)
    s2set = fys_ep(sidx2) if sidx2 else set()
    sjuk2 = np.array([1.0 if (d in s2set or iso(int(o) + 1) in s2set) else 0.0
                      for d, o in zip(K.dates, K.o)])
    j_sjuk = BASE.index('sjuk') + 1

    def fyll(v):
        """Ersätt saknade med medianen ur de första T0 dygnen. Saknas variabeln (nästan) helt: None,
        och den lämnas utanför (en kolumn med bara samma värde går inte att skatta)."""
        andel = float(np.mean(~np.isfinite(v)))
        if np.isfinite(v).sum() < 30:
            return None, andel
        bas = v[:t0][np.isfinite(v[:t0])]
        med = float(np.median(bas)) if len(bas) else float(np.nanmedian(v))
        return np.where(np.isfinite(v), v, med), andel
    somn, saknas_somn = fyll(K.d['somn_h'])
    vaken, saknas_vaken = fyll(K.d['vaken_min'])
    sovn = tuple(x for x in (somn, vaken) if x is not None)

    def X(keys, sjukkol=None, extra=()):
        M = K.design(keys, np.arange(N))
        if sjukkol is not None and 'sjuk' in keys:
            M = M.copy()
            M[:, keys.index('sjuk') + 1] = sjukkol
        return np.column_stack([M, *extra]) if extra else M

    def r2(M):
        p, m = expanding_snabb(M, y, t0)
        return K.r2(p, m)

    utan_h7 = [k for k in BASE if k != 'h7']
    r = {
        "bas": r2(X(BASE)),
        "bas_sjuk2": r2(X(BASE, sjuk2)),
        "bas_somn": r2(X(BASE, None, sovn)),
        "bas_sjuk2_somn": r2(X(BASE, sjuk2, sovn)),
        "form": r2(X(BASE, None, (form,))),
        "form_sjuk2_somn": r2(X(BASE, sjuk2, (form, *sovn))),
        "utan_h7": r2(X(utan_h7)),
        "utan_h7_sjuk2_somn": r2(X(utan_h7, sjuk2, sovn)),
    }
    b, se, _ = ols(X(BASE, sjuk2, (form, *sovn)), y)
    koef = {}
    j = len(b) - len(sovn)
    for namn, v, skala in (("somn_per_h", somn, 1), ("vaken_per_10min", vaken, 10)):
        if v is None:
            koef[namn] = None
        else:
            koef[namn] = {"b": float(b[j]) * skala, "se": float(se[j]) * skala}
            j += 1
    sjuk1 = K.d['sjuk']
    return {
        "r2": r,
        "vinst_mot_form": r["form_sjuk2_somn"] - r["form"],
        "vinst_utan_h7": r["utan_h7_sjuk2_somn"] - r["utan_h7"],
        **koef,
        "sjuk2": {"b": float(b[j_sjuk]), "se": float(se[j_sjuk])},
        "andel_sjuk1": float(sjuk1.mean()), "andel_sjuk2": float(sjuk2.mean()),
        "sjuk1_kvar_i_sjuk2": float((sjuk2[sjuk1 == 1] == 1).mean()) if sjuk1.any() else None,
        "saknas_somn": saknas_somn, "saknas_vaken": saknas_vaken,
        "pass_utan_last_dygn": int(sum(1 for d in K.dates if d in A and
                                      sum((a['load'] or 0) for a in A[d]) == 0)),
        "somn_median_h": float(np.median(somn)) if somn is not None else None,
        "vaken_median_min": float(np.median(vaken)) if vaken is not None else None,
    }


# ----------------------------------------------------------------- minuter oavsett puls (punkt 6)
def minut_variant(K, form) -> dict:
    """Punkt 6, som variant vid sidan av basmodellen: pass som klockan ger nästan ingen last
    (fridykning, vandring, yoga) men som ändå kan kosta. Två egna termer prövas var för sig:
      - emin: alla uthållighetsminuter samma dag, oavsett puls
      - lmin: minuter i lågintensiva pass (last per minut < LAGINT)
    Utöver basmodellen och utöver basmodellen med form, ut ur stickprovet på samma dygn."""
    y, N, t0 = K.y, K.N, K.t0
    Xb = K.design(K.spec, np.arange(N))

    def r2(M):
        p, m = expanding_snabb(M, y, t0)
        return K.r2(p, m)

    emin, lmin = K.d['emin'], K.d['lmin']
    r = {"bas": r2(Xb), "form": r2(np.column_stack([Xb, form]))}
    ut = {"r2": r, "andel_lagint_min": float(lmin.sum() / emin.sum()) if emin.sum() > 0 else None,
          "dygn_med_lagint": int((lmin > 0).sum()), "lagint_grans": LAGINT}
    for namn, v in (("emin", emin), ("lmin", lmin)):
        if (v > 0).sum() < 15:
            ut[namn] = None
            continue
        r[f"bas_{namn}"] = r2(np.column_stack([Xb, v]))
        r[f"form_{namn}"] = r2(np.column_stack([Xb, form, v]))
        b, se, _ = ols(np.column_stack([Xb, form, v]), y)
        ut[namn] = {"per_timme": {"b": float(b[-1]) * 60, "se": float(se[-1]) * 60},
                    "vinst_mot_bas": r[f"bas_{namn}"] - r["bas"], "vinst_mot_form": r[f"form_{namn}"] - r["form"]}
    return ut


# ----------------------------------------------------------------- hela körningen
def kor(con: sqlite3.Connection, start: str | None = None, t0: int | None = None,
        progress=None, utfall: str | None = None) -> dict:
    """Kör kedjan. Returnerar en dict med serier (för dashboarden) och nyckeltal (för bidraget).
    Vid för lite data: {'ok': False, 'skal': ...}.

    utfall: 'hrv', 'vilopuls' eller None. None väljer själv: HRV när det räcker till modellen, annars
    vilopulsen om klockan mätt den tillräckligt länge (punkt 10b: klockor utan HRV-status, eller en ny
    klocka med kort HRV-historik). Resultatet har R['utfall'] och R['enhet'] ('ms' eller 'slag/min')."""
    tick = progress or (lambda *_: None)
    D, A = las(con)
    if utfall is not None:
        return _kor(D, A, start, t0, tick, utfall)
    R = _kor(D, A, start, t0, tick, 'hrv')
    if R['ok']:
        return R
    Rv = _kor(D, A, start, t0, tick, 'vilopuls')
    if Rv['ok']:
        Rv['varfor_vilopuls'] = R['skal']
        return Rv
    if R.get('saknar_hrv_status'):
        R['skal'] += (' Modellen kan i stället räknas på vilopulsen när det finns minst '
                      f'{MIN_RADER} dygn med vilopuls och stress. Nu finns {Rv.get("n", 0)}.')
    return R


def _kor(D, A, start, t0, tick, utfall) -> dict:
    vp = utfall == 'vilopuls'
    spec, total = (BASE_VP, TOTAL_VP) if vp else (BASE, TOTAL)
    first = forsta_vilopuls(D) if vp else forsta_hrv(D)
    if first is None:
        if vp:
            return {'ok': False, 'n': 0, 'skal': 'Ingen vilopuls i databasen ännu.'}
        n_rhr = sum(1 for v in D.values() if v['rhr'] is not None)
        if n_rhr >= 30:
            # Vilopuls finns men ingen HRV: klockan saknar troligen HRV-status (punkt 10)
            return {'ok': False, 'saknar_hrv_status': True, 'skal': (
                f'Klockan har mätt vilopuls {n_rhr} nätter men ingen nattlig HRV. Den saknar troligen '
                'HRV-status, till exempel Venu Sq, Venu 2 (inte Plus), Vivoactive 4, Vivosmart 5, Fenix 6 eller '
                'äldre Forerunner. Se listan i LÄSMIG. Vilopuls, sömn, stress och träning visas ändå under '
                'Hälsa och Träning.')}
        return {'ok': False, 'skal': 'Ingen nattlig HRV i databasen ännu.'}
    start = max(start or first, first)
    tick('Sjukindex', 0.05)
    # Vilopulsvägen: sjukflaggan bara ur andningen (vilopulsen är ju utfallet, se sjukindex)
    sidx = sjukindex(D, A, start, bara_andning=vp)
    sjuk = fys_ep(sidx) if sidx else set()
    rows = bygg_rader(D, A, start, sjuk, utfall)
    if len(rows) < MIN_RADER:
        vad = 'vilopuls, stress och vilopulsens veckosnitt' if vp else 'HRV, stress och HRV-veckosnitt'
        return {'ok': False, 'n': len(rows),
                'skal': f'{len(rows)} användbara dygn med {vad}. '
                        f'Modellen behöver minst {MIN_RADER}, helst 400 eller fler.'}

    K = Kedja(rows, t0, spec)
    tick('Basmodellen', 0.10)
    p0, m0 = K.expanding(spec)
    res = K.residualer(p0)

    scan, best = {}, None
    for i, hl in enumerate(HALVERINGSTIDER):
        tick(f'Långsam form, halveringstid {hl} d', 0.15 + 0.6 * i / len(HALVERINGSTIDER))
        f = K.ewma_feature(res, hl)
        p, m = K.expanding(spec, extra=[f])
        v = K.r2(p, m)
        scan[hl] = v
        if best is None or v > best[1]:
            best = (hl, v, f, p, m)
    hl, r2_form, form, p_form, m_form = best

    tick('Jämförelser', 0.8)
    utan_h7 = spec[1:]     # utan nivåtermen (h7 eller r7)
    p_nh7, m_nh7 = K.expanding(utan_h7)
    p_nh7f, m_nh7f = K.expanding(utan_h7, extra=[form])

    # Helsample-koefficienter i slutmodellen (direkt effekt) och utan stress (total effekt)
    N = K.N
    Xs = np.column_stack([np.ones(N)] + [K.d[k] for k in spec] + [form])
    b, se, n_ols = ols(Xs, K.y)
    koef = {k: {'b': float(b[i + 1]), 'se': float(se[i + 1])} for i, k in enumerate(spec + ['form'])}
    Xt = np.column_stack([np.ones(N)] + [K.d[k] for k in total] + [form])
    bt, set_, _ = ols(Xt, K.y)
    koef_total = {k: {'b': float(bt[i + 1]), 'se': float(set_[i + 1])} for i, k in enumerate(total + ['form'])}
    suff = {'direkt': tillracklig(Xs, K.y, spec + ['form']), 'total': tillracklig(Xt, K.y, total + ['form'])}

    tick('Lång lastlagg', 0.85)
    ll = lang_last(K, A, form)
    tick('Sömn', 0.88)
    # Sömnvarianten bygger på sjukindex 2 med vilopulsen, så den räknas bara för HRV
    sv = None if vp else somn_variant(K, D, A, form, start)
    mv = minut_variant(K, form)
    tick('Nyckeltal', 0.9)
    ext = extra_nyckeltal(K, D, A, form, res, sjuk, start)
    tick('Klart', 1.0)
    return {
        'ok': True, 'utfall': utfall, 'enhet': 'slag/min' if vp else 'ms', 'spec': spec,
        'start': start, 't0': K.t0, 'n': N, 'dates': K.dates, 'rows': rows,
        'y': K.y, 'pred_base': p0, 'pred_form': p_form, 'res': res, 'form': form, 'hl': hl,
        'sjuk': sorted(sjuk),
        'r2': {'bas': K.r2(p0, m0), 'form': r2_form, 'utan_h7': K.r2(p_nh7, m_nh7),
               'utan_h7_form': K.r2(p_nh7f, m_nh7f)},
        'mae': {'bas': K.mae(p0, m0), 'form': K.mae(p_form, m_form)},
        'n_utv': int(m0.sum()),
        'scan': scan, 'koef': koef, 'koef_total': koef_total, 'n_ols': n_ols, 'suff': suff, 'lang_last': ll, 'somn': sv, 'minuter': mv,
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
    dev = y - d[K.spec[0]]     # mot nivåtermen: HRV-veckosnittet, eller vilopulsens 7-dygnsmedel
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
    vp = K.spec is BASE_VP
    out['beskrivning'] = {
        ('vilopuls_medel' if vp else 'hrv_medel'): float(y.mean()),
        ('vilopuls_sd' if vp else 'hrv_sd'): float(y.std(ddof=1)),
        'stress_medel': float(d['stress'].mean()), 'stress_sd': float(d['stress'].std(ddof=1)),
        'last_per_dygn': float(alla_last.mean()) if len(alla_last) else None,
        'andel_passdygn': float((d['load'] > 0).mean()),
        'andel_sjukdygn': float(d['sjuk'].mean()),
        'andel_styrkedygn': float((d['smin'] > 0).mean()),
        'typfordelning_min': {g: round(v / tot, 3) for g, v in typmin.items() if v > 0},
        'dygn_i_serien': int(K.o[-1] - K.o[0] + 1),
        'startar': start[:7],
        'sjukindex_finns': bool(sjuk) or any(D[x]['respiration_avg'] is not None for x in sd),
        **({'sjukindex': 'andning'} if vp else {}),
    }
    return out


if __name__ == '__main__':
    import sys
    import time
    arg = [a for a in sys.argv[1:] if not a.startswith('--')]
    path = arg[0] if arg else 'data/hrv.db'
    start = arg[1] if len(arg) > 1 else None
    t = time.time()
    R = kor(sqlite3.connect(path), start=start, progress=lambda s, f: print(f'  [{f:4.0%}] {s}', flush=True),
            utfall='vilopuls' if '--vilopuls' in sys.argv else None)
    if not R['ok']:
        print(R['skal'])
        sys.exit(1)
    if R['utfall'] != 'hrv':
        print(f"UTFALL: {R['utfall']} ({R['enhet']}, högre = sämre)", R.get('varfor_vilopuls', ''))
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
    L = R['lang_last']
    bn, tr = L['banister'], L['troskel']
    print(f"lång lastlagg: bas {L['r2_bas']:.4f}  form {L['r2_form']:.4f}  | Banister bäst {bn['fitness_hl']}/"
          f"{bn['trotthet_hl']} d: {bn['r2']:.4f} ({bn['vinst_mot_bas']:+.4f} mot bas), med form {bn['r2_med_form']:.4f} "
          f"({bn['vinst_mot_form']:+.4f} mot form)  fitness {bn['fitness']['b']:+.4f} (t {bn['fitness']['b'] / bn['fitness']['se']:+.1f})  "
          f"trötthet {bn['trotthet']['b']:+.4f} (t {bn['trotthet']['b'] / bn['trotthet']['se']:+.1f})")
    S = R['somn']
    if S: print(f"sömnvariant: R² {', '.join(f'{k} {v:.4f}' for k, v in S['r2'].items())}")
    kt = lambda k, e: "saknas" if not k else f"{k['b']:+.2f} {e} (t {k['b'] / k['se']:+.1f})"
    if S: print(f"  vinst mot form {S['vinst_mot_form']:+.4f}, utan h7 {S['vinst_utan_h7']:+.4f} | sömn {kt(S['somn_per_h'], 'ms/h')}, "
          f"vaken {kt(S['vaken_per_10min'], 'ms/10 min')} | sjukflagga {S['andel_sjuk1']:.1%} → "
          f"{S['andel_sjuk2']:.1%}, kvar {S['sjuk1_kvar_i_sjuk2']:.0%} | pass utan last {S['pass_utan_last_dygn']} dygn")
    M = R['minuter']
    for k, lab in (("emin", "alla uthållighetsminuter"), ("lmin", "lågintensiva minuter")):
        v = M[k]
        print(f"minutvariant {lab}: " + ("för få dygn" if not v else
              f"{v['per_timme']['b']:+.2f} {R['enhet']}/h (t {v['per_timme']['b'] / v['per_timme']['se']:+.1f}), "
              f"vinst mot bas {v['vinst_mot_bas']:+.4f}, mot form {v['vinst_mot_form']:+.4f}"))
    if M['andel_lagint_min'] is not None:
        print(f"  lågintensivt: {M['andel_lagint_min']:.1%} av uthållighetsminuterna, {M['dygn_med_lagint']} dygn")
    if tr:
        print(f"  tröskel {tr['knut']}: {tr['r2_med_form']:.4f} ({tr['vinst_mot_form']:+.4f} mot form)  extra lutning över "
              f"{tr['over_knut_extra']['b']:+.4f} (t {tr['over_knut_extra']['b'] / tr['over_knut_extra']['se']:+.1f}), n över {tr['n_over']}")
