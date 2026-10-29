# -*- coding: utf-8 -*-
"""Träningslabbet (punkt 12): simulera en veckomall som upprepas i 4–12 veckor.

Återhämtningen räknas REKURSIVT dag för dag med BASE-koefficienterna ur huvudmodellen:
    natt efter dag t = a + b_h7·h7(t) + b_stress·stress(t) + b_pstress·stress(t−1) + b_load·last(t)
                       + b_pload·last(t−1) + b_hour·starttimme(t) + b_smin·styrkeminuter(t) + b_sjuk·sjuk(t)
                       + b_form·(formens medel)
där h7(t) är medel av de sju SIMULERADE nätterna före. Det är rekursionen som ger ackumuleringen: tre hårda
dagar sänker nästa veckas utgångsläge. Den långsamma formen hålls utanför (sätts till sitt medel).

Utfallet visas som avvikelse från personens egen nivå: modellens jämviktsnivå när allt ligger på det
historiska medlet och personen är frisk. Osäkerhetsbandet kommer ur koefficienternas kovarians (dragningar
ur normalfördelningen, var och en mot sin egen jämviktsnivå, så att bandet visar osäkerheten i planens
effekt och inte i nivån).

Stressen i planen är "övrig stress" (dagsstress rensad från det påslag träningen ger, se planering.py).
Den simulerade dagsstressen = övrig stress + a1·dagens last + a2·gårdagens last, plus nedvarvningsratten.

Fitness är generisk: EWMA av lasten med 42 dygns tidskonstant (CTL i TrainingPeaks, samma idé som Stravas
Fitness), i lastenheter. Den kopplas ALDRIG till HRV här (punkt 4).

Ren numpy, så att rattarna svarar direkt."""
import datetime as dt

import numpy as np

import modell

FITNESS_TAU = 42      # dygn, CTL-konventionen
N_DRAG = 300          # dragningar ur koefficienternas kovarians
NEDVARVNING = {"Sämre än vanligt": 0.5, "Som du brukar": 0.0, "Bättre än vanligt": -0.5}  # sd övrig stress dagen efter pass
STRESSNIVAER = ["lugn", "medel", "tung", "mycket tung"]


class Labb:
    def __init__(self, R: dict, P, PT: dict | None = None, seed: int = 0):
        """R = modell.kor, P = planering.Planerare (stressdelningen, nivåerna, spärren), PT = passtyper.bygg."""
        self.R, self.P = R, P
        S = R["suff"]["direkt"]
        self.namn = S["namn"]                                    # konst, h7, stress, ..., form
        XtX, Xty = np.array(S["ra"]["XtX"]), np.array(S["ra"]["Xty"])
        n, p = S["n"], len(self.namn)
        self.b = np.linalg.solve(XtX, Xty)
        s2 = (S["ra"]["yty"] - self.b @ Xty) / (n - p)
        self.V = s2 * np.linalg.inv(XtX)
        self.i = {k: j for j, k in enumerate(self.namn)}
        niva = "r7" if R.get("utfall") == "vilopuls" else "h7"
        self.niva = niva

        rows = R["rows"]
        col = lambda k: np.array([r[k] for r in rows], float)
        # Historiska medel (sjuk = 0: nivån gäller en frisk person)
        self.medel = {k: float(np.nanmean(col(k))) for k in ("stress", "pstress", "load", "pload", "hour", "smin")}
        self.medel["sjuk"] = 0.0
        self.medel["form"] = float(np.mean(R["form"]))
        self.medel_last = self.medel["load"]
        self.vilotimme = 12.0                                     # rows har 12 på dygn utan pass

        rng = np.random.default_rng(seed)
        self.drag = np.vstack([self.b, rng.multivariate_normal(self.b, self.V, size=N_DRAG, method="eigh")])
        self.jamvikt = self._jamvikt(self.drag)

        # p10: tionde percentilen av nätterna senaste året, mot årets medel
        y = np.array(R["y"], float)[-365:]
        self.p10 = float(np.percentile(y - y.mean(), 10))

        # Övrig stress: nivåer i personens egna enheter, förvalt = eget medel
        ovr = np.array([v for d, v in P.ovr.items() if d not in set(R["sjuk"])], float)
        self.stressniva = {"lugn": P.niva["lugn"], "medel": float(ovr[-365:].mean()), "tung": P.niva["tung"],
                           "mycket tung": P.niva["mycket tung"]}
        self.ovr_sd = float(ovr[-365:].std(ddof=1))
        self.a1, self.a2 = P.a1, P.a2

        self.fitness_nu = self._fitness_nu(P)
        self.typer = self._typer(PT)
        self.mall_vanlig = self._vanlig_mall(PT)

    # ------------------------------------------------------------ hjälp
    def _jamvikt(self, B: np.ndarray) -> np.ndarray:
        """Jämviktsnivån per dragning: alla termer på sitt medel, h7 = nivån själv."""
        rest = B[:, self.i["konst"]].copy()
        for k, j in self.i.items():
            if k in ("konst", self.niva):
                continue
            rest += B[:, j] * self.medel[k]
        return rest / (1 - B[:, self.i[self.niva]])

    def _fitness_nu(self, P) -> float:
        slut = modell.ordn(P.slut)
        last = [P.load.get(modell.iso(slut - k), 0.0) for k in range(89, -1, -1)]
        f = float(np.mean(last[:FITNESS_TAU]))
        for x in last:
            f += (x - f) / FITNESS_TAU
        return f

    def _typer(self, PT) -> dict:
        """Passtyperna (punkt 8) som val i mallen: medianlast, mediantimme, styrkeminuter."""
        if not PT or not PT.get("ok"):
            return {}
        ut = {}
        for j, namn in PT["namn"].items():
            P_ = [p for p in PT["pass"] if p["kluster"] == j]
            last = np.array([p["load"] for p in P_], float)
            tim = np.array([p["timme"] for p in P_], float)
            ut[f"Typ {j + 1}: {namn}"] = {
                "last": float(np.nanmedian(last)) if np.isfinite(last).any() else 0.0,
                "timme": float(np.nanmedian(tim)) if np.isfinite(tim).any() else 17.0,
                "smin": float(np.mean([p["min"] if p["grupp"] == "styrka" else 0.0 for p in P_])),
                "n": len(P_)}
        return ut

    def _vanlig_mall(self, PT, veckor: int = 8) -> list[dict]:
        """Förvald mall: per veckodag den vanligaste passtypen de senaste åtta veckorna, om personen tränat
        den veckodagen minst halva veckorna, annars vila."""
        mall = [{"typ": "Vila"} for _ in range(7)]
        if not self.typer:
            return mall
        slut = modell.ordn(self.P.slut)
        senaste = [p for p in PT["pass"] if modell.ordn(p["date"]) > slut - 7 * veckor]
        for w in range(7):
            dag = [p for p in senaste if dt.date.fromisoformat(p["date"]).weekday() == w]
            if len({p["date"] for p in dag}) * 2 < veckor:
                continue
            j = max(set(p["kluster"] for p in dag), key=lambda k: sum(p["kluster"] == k for p in dag))
            namn = f"Typ {j + 1}: {PT['namn'][j]}"
            tim = np.nanmedian([p["timme"] for p in dag if p["kluster"] == j])
            mall[w] = {"typ": namn, "timme": float(round(tim)) if np.isfinite(tim) else round(self.typer[namn]["timme"])}
        return mall

    def dag(self, val: dict) -> dict:
        """Ett val i mallen → modellens indata för dagen."""
        if val["typ"] == "Vila":
            return {"last": 0.0, "smin": 0.0, "timme": self.vilotimme}
        if val["typ"] == "Egen last":
            return {"last": float(val.get("last", 0)), "smin": float(val.get("smin", 0)),
                    "timme": float(val.get("timme", 17))}
        t = self.typer[val["typ"]]
        return {"last": t["last"], "smin": t["smin"], "timme": float(val.get("timme", t["timme"]))}

    # ------------------------------------------------------------ simulering
    def indata(self, mall: list[dict], veckor: int, stress_vecka: list[str], nedvarvning: str = "Som du brukar",
               sjuk_vecka: int | None = None, sjuk_dagar: int = 4) -> dict:
        """Dygnsvisa indata för hela horisonten. Mallens dag 0 = måndag. Sjukdagar: ingen träning."""
        N = 7 * veckor
        dagar = [self.dag(mall[t % 7]) for t in range(N)]
        last = np.array([d["last"] for d in dagar])
        smin = np.array([d["smin"] for d in dagar])
        timme = np.array([d["timme"] for d in dagar])
        sjuk = np.zeros(N)
        if sjuk_vecka is not None:
            s = 7 * sjuk_vecka
            sjuk[s:s + sjuk_dagar] = 1.0
            last[s:s + sjuk_dagar], smin[s:s + sjuk_dagar], timme[s:s + sjuk_dagar] = 0.0, 0.0, self.vilotimme
        plast = np.r_[last[-1] if veckor else 0.0, last[:-1]]      # mallen antas redan pågå före start
        ovr = np.array([self.stressniva[stress_vecka[t // 7]] for t in range(N)])
        stress = ovr + self.a1 * last + self.a2 * plast + NEDVARVNING[nedvarvning] * self.ovr_sd * (plast > 0)
        pstress = np.r_[stress[-1], stress[:-1]]
        return {"load": last, "pload": plast, "smin": smin, "hour": timme, "sjuk": sjuk,
                "stress": stress, "pstress": pstress}

    def simulera(self, X: dict, start_avv: np.ndarray | None = None, form: np.ndarray | None = None) -> np.ndarray:
        """Nätterna efter varje dag, alla dragningar (rad 0 = punktskattningen), som avvikelse från jämvikten.
        start_avv: de sju nätterna före start som avvikelse (förvalt noll = egen nivå)."""
        B = self.drag
        N = len(X["load"])
        hist = np.zeros((len(B), 7)) if start_avv is None else np.tile(np.asarray(start_avv, float), (len(B), 1))
        hist = hist + self.jamvikt[:, None]
        ut = np.zeros((len(B), N))
        fast = B[:, self.i["konst"]] + B[:, self.i["form"]] * self.medel["form"]
        for t in range(N):
            y = fast + B[:, self.i[self.niva]] * hist.mean(1)
            for k in ("stress", "pstress", "load", "pload", "hour", "smin", "sjuk"):
                y = y + B[:, self.i[k]] * X[k][t]
            if form is not None:
                y = y + B[:, self.i["form"]] * (form[t] - self.medel["form"])
            ut[:, t] = y
            hist = np.c_[hist[:, 1:], y]
        return ut - self.jamvikt[:, None]

    def fitness(self, last: np.ndarray) -> np.ndarray:
        f, ut = self.fitness_nu, []
        for x in last:
            f += (x - f) / FITNESS_TAU
            ut.append(f)
        return np.array(ut)

    def kor(self, mall, veckor, stress_vecka, nedvarvning="Som du brukar", sjuk_vecka=None) -> dict:
        X = self.indata(mall, veckor, stress_vecka, nedvarvning, sjuk_vecka)
        Y = self.simulera(X)
        fit = self.fitness(X["load"])
        return {"medel": Y[0], "lo": np.percentile(Y[1:], 10, axis=0), "hi": np.percentile(Y[1:], 90, axis=0),
                "indata": X, "fitness": fit, "snitt": float(Y[0].mean()),
                "under_p10": int((Y[0] < self.p10).sum()), "n": len(Y[0]),
                "fitness_fran": self.fitness_nu, "fitness_till": float(fit[-1]),
                "veckolast": float(X["load"][:7].sum())}

    def text(self, S: dict, enh: str = "ms") -> str:
        f = lambda x, n=1: f"{x:+.{n}f}".replace("-", "−").replace(".", ",")
        g = lambda x: f"{x:.0f}"
        return (f"Planen ger i snitt {f(S['snitt'])} {enh} per natt mot din vanliga nivå och når din p10 "
                f"{S['under_p10']} nätter av {S['n']}. Fitness går från {g(S['fitness_fran'])} till "
                f"{g(S['fitness_till'])}.")
