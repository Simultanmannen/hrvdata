# -*- coding: utf-8 -*-
"""Passtyper (punkt 8 och 9): PCA över alla pass och k-means-kluster, med HRV-kostnaden per kluster.

Bara beskrivning, inte modell. Variablerna per pass (de som saknas hos personen tas bort):
längd (log-minuter), last (log), last per minut, aerob och anaerob TE, medel- och maxpuls, starttimme.
Sporten är ingen variabel utan används för att färga punkterna, liksom veckodag, månad och år.

Kostnad per kluster = HRV natten efter minus 7-dygnssnittet, jämfört med vilodygn. Bara dygn med ett
enda pass räknas (annars blandas passtyperna), och sjukdygn tas bort.

Ren numpy (PCA med SVD, egen k-means), eftersom appen inte har scikit-learn."""
import datetime as dt

import numpy as np

import modell

VARIABLER = [("min", "Längd"), ("load", "Last"), ("lpm", "Last per minut"), ("ate", "Aerob TE"),
             ("ante", "Anaerob TE"), ("hr", "Medelpuls"), ("hrmax", "Maxpuls"), ("timme", "Starttimme")]
MIN_PASS = 40       # färre pass än så: ingen typologi
MIN_MINUTER = 10    # kortare pass (t.ex. felstartade) räknas inte
VECKODAGAR = ["mån", "tis", "ons", "tor", "fre", "lör", "sön"]
SASONG = {12: "vinter", 1: "vinter", 2: "vinter", 3: "vår", 4: "vår", 5: "vår", 6: "sommar", 7: "sommar",
          8: "sommar", 9: "höst", 10: "höst", 11: "höst"}


def _kmeans(Z, k, rng, n_init=10, iters=100):
    """k-means++ start och Lloyd, bästa av n_init körningar (lägst summa kvadrater)."""
    bast = None
    n = len(Z)
    for _ in range(n_init):
        c = [Z[rng.integers(n)]]
        for _ in range(1, k):
            d2 = np.min(((Z[:, None, :] - np.array(c)[None]) ** 2).sum(-1), axis=1)
            c.append(Z[rng.choice(n, p=d2 / d2.sum())])
        C = np.array(c)
        for _ in range(iters):
            lab = np.argmin(((Z[:, None, :] - C[None]) ** 2).sum(-1), axis=1)
            C_ny = np.array([Z[lab == j].mean(0) if (lab == j).any() else C[j] for j in range(k)])
            if np.allclose(C_ny, C):
                break
            C = C_ny
        sse = float(((Z - C[lab]) ** 2).sum())
        if bast is None or sse < bast[0]:
            bast = (sse, lab, C)
    return bast[1], bast[2]


def _silhuett(Z, lab):
    """Medelsilhuett. O(n²) i minne, därför stickprov på högst 1500 pass."""
    n = len(Z)
    idx = np.arange(n) if n <= 1500 else np.random.default_rng(0).choice(n, 1500, replace=False)
    Zs, ls = Z[idx], lab[idx]
    nrm = (Zs ** 2).sum(1)
    Dm = np.sqrt(np.maximum(nrm[:, None] + nrm[None] - 2 * Zs @ Zs.T, 0.0))
    s = []
    for i in range(len(Zs)):
        egen = ls == ls[i]
        if egen.sum() <= 1:
            s.append(0.0)
            continue
        a = Dm[i, egen].sum() / (egen.sum() - 1)
        b = min(Dm[i, ls == j].mean() for j in set(ls) if j != ls[i])
        s.append((b - a) / max(a, b))
    return float(np.mean(s))


def bygg(con, sjuk: list[str] | set[str] = (), seed: int = 0) -> dict:
    D, A = modell.las(con)
    sjuk = set(sjuk)
    pass_ = []
    for d, acts in A.items():
        for a in acts:
            if not a["dur"] or a["dur"] < MIN_MINUTER * 60:
                continue
            try:
                t = dt.datetime.fromisoformat(str(a["start"]).replace(" ", "T"))
                timme = t.hour + t.minute / 60
            except (ValueError, TypeError):
                timme = np.nan
            m = a["dur"] / 60
            load = a["load"] if a["load"] is not None else np.nan
            pass_.append({"date": d, "typ": a["type"], "grupp": modell.grupp(a["type"]),
                          "min": m, "load": load, "lpm": load / m if np.isfinite(load) else np.nan,
                          "ate": a["ate"] if a["ate"] is not None else np.nan,
                          "ante": a["ante"] if a["ante"] is not None else np.nan,
                          "hr": a["hr"] if a["hr"] else np.nan, "hrmax": a["hrmax"] if a["hrmax"] else np.nan,
                          "timme": timme, "n_pass_dygn": len(acts)})
    if len(pass_) < MIN_PASS:
        return {"ok": False, "skal": f"{len(pass_)} pass på minst {MIN_MINUTER} minuter. Passtyperna behöver minst {MIN_PASS}."}

    # Variabler: ta bort de som saknas för mer än hälften av passen, fyll resten med medianen
    anvanda, kol = [], []
    for k, namn in VARIABLER:
        v = np.array([p[k] for p in pass_], float)
        if np.isfinite(v).mean() < 0.5 or np.nanstd(v) == 0:
            continue
        v = np.where(np.isfinite(v), v, np.nanmedian(v))
        if k == "min":
            v = np.log(v)
        elif k == "load":
            v = np.log1p(np.maximum(v, 0))
        anvanda.append((k, namn))
        kol.append(v)
    X = np.column_stack(kol)
    Z = (X - X.mean(0)) / X.std(0)

    # PCA
    U, S, Vt = np.linalg.svd(Z, full_matrices=False)
    forklarad = S ** 2 / (S ** 2).sum()
    poang = U * S
    for j in range(Vt.shape[0]):          # tecken: största laddningen positiv, så PC1 = "mer av"
        if Vt[j, np.argmax(np.abs(Vt[j]))] < 0:
            Vt[j] *= -1
            poang[:, j] *= -1

    # k-means, antal kluster ur silhuetten (3–6)
    rng = np.random.default_rng(seed)
    val = {}
    for k in range(3, 7):
        if len(Z) < 10 * k:
            break
        lab, C = _kmeans(Z, k, rng)
        val[k] = (_silhuett(Z, lab), lab)
    k_bast = max(val, key=lambda k: val[k][0])
    lab = val[k_bast][1]

    # Utfall: HRV natten efter minus 7-dygnssnittet, mot vilodygn (utan sjukdygn)
    def avv(d):
        n1 = modell.iso(modell.ordn(d) + 1)
        h, h7 = (D.get(n1) or {}).get("hrv_night"), D[d].get("hrv_7d") if d in D else None
        return h - h7 if h is not None and h7 is not None else np.nan
    vila = [avv(d) for d in D if d not in A and d not in sjuk]
    vila = np.array([v for v in vila if np.isfinite(v)])
    vila_m = float(vila.mean()) if len(vila) else 0.0
    for p, l in zip(pass_, lab):
        p["kluster"] = int(l)
        p["avv"] = avv(p["date"]) if p["date"] not in sjuk else np.nan

    # Namnge och summera kluster
    alla_min = np.array([p["min"] for p in pass_])
    alla_lpm = np.array([p["lpm"] for p in pass_], float)
    t_min = np.percentile(alla_min, [33, 67])
    t_lpm = np.nanpercentile(alla_lpm, [33, 67]) if np.isfinite(alla_lpm).any() else None
    # Namnen sätts på hela historiken och följer med när tabellen räknas för ett urval
    namn = {}
    for j in range(k_bast):
        P = [p for p in pass_ if p["kluster"] == j]
        m_min = float(np.median([p["min"] for p in P]))
        m_lpm = float(np.nanmedian([p["lpm"] for p in P])) if t_lpm is not None else np.nan
        langd = "långa" if m_min > t_min[1] else ("korta" if m_min < t_min[0] else "medellånga")
        intens = "" if t_lpm is None else ("hårda" if m_lpm > t_lpm[1] else ("lätta" if m_lpm < t_lpm[0] else "medelhårda"))
        namn[j] = f"{langd}, {intens}".strip(", ") if intens else langd
    res = {"ok": True, "pass": pass_, "poang": poang[:, :2], "forklarad": forklarad.tolist(),
           "laddningar": {n: Vt[:2, i].tolist() for i, (_, n) in enumerate(anvanda)},
           "variabler": [n for _, n in anvanda], "k": k_bast, "namn": namn,
           "silhuett": {k: v[0] for k, v in val.items()}, "vila_avv": vila_m,
           "vila_var": float(vila.var(ddof=1)) if len(vila) > 1 else 0.0, "n_vila": int(len(vila))}
    res["kluster"] = sammanfatta(res)
    return res


def sammanfatta(res: dict, urval=None) -> list[dict]:
    """Klustertabellen för ett urval av pass (bool per pass, i samma ordning som res['pass']), med namnen
    från hela historiken. None = alla pass. Används för att tabellen ska följa tidsväljaren och sportfiltret.
    Kostnad = HRV natten efter minus 7-dygnssnittet, jämfört med vilodygn (vilodygnen alltid hela historiken)."""
    pass_ = res["pass"] if urval is None else [p for p, u in zip(res["pass"], urval) if u]
    ut = []
    for j in range(res["k"]):
        P = [p for p in pass_ if p["kluster"] == j]
        if not P:
            continue
        grupper = {}
        for p in P:
            grupper[p["grupp"]] = grupper.get(p["grupp"], 0) + 1
        enkla = np.array([p["avv"] for p in P if p["n_pass_dygn"] == 1 and np.isfinite(p["avv"])])
        last = np.array([p["load"] for p in P], float)
        kostnad = float(enkla.mean() - res["vila_avv"]) if len(enkla) >= 5 else None
        se = float(np.sqrt(enkla.var(ddof=1) / len(enkla) + (res["vila_var"] / res["n_vila"] if res["n_vila"] > 1 else 0)))\
            if len(enkla) >= 5 else None
        ut.append({
            "id": j, "n": len(P), "n_enkla": int(len(enkla)),
            "topp": sorted(grupper.items(), key=lambda x: -x[1])[:3], "beskrivning": res["namn"][j],
            "median_min": float(np.median([p["min"] for p in P])),
            "median_last": float(np.nanmedian(last)) if np.isfinite(last).any() else None,
            "kostnad": kostnad, "kostnad_se": se,
        })
    return ut


def pc_namn(res: dict, j: int) -> str:
    """Kort beskrivning av en huvudkomponent ur de två största laddningarna."""
    L = sorted(((abs(v[j]), n, v[j]) for n, v in res["laddningar"].items()), reverse=True)[:2]
    return " + ".join(("" if s > 0 else "−") + n.lower() for _, n, s in L)
