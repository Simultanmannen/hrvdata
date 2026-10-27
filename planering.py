# -*- coding: utf-8 -*-
"""Veckoplaneraren: lägg veckans vanliga träning så att HRV håller sig i sitt normalspann.

Utgångspunkt: målet är en KONSEKVENT träningsnivå, alltså personens egen typiska vecka (median
av de senaste åtta veckorna). Den förändras inte. Det planeraren väljer är vilken dag varje pass
ligger, givet hur stressig varje dag förväntas bli.

Varför det spelar roll trots att modellen är linjär: veckans MEDEL-HRV blir densamma hur passen
än läggs. Men nätterna blir olika. Ett hårt pass på en tung jobbdag ger en natt långt under
normalspannet, samma pass på en lugn dag ger två måttliga nätter. Planeraren minimerar därför
det förväntade antalet nätter under normalspannet, med modellens osäkerhet inräknad.

Planeringsmodellen skattas på samma rader som huvudmodellen, men stressen delas upp:
  övrig stress(d) = dagsstress(d) − a1·last(d) − a2·last(d−1)
dvs. stressen rensad från det påslag träningen själv ger. Användarens skattning gäller övrig
stress, och lastens koefficient blir då lastens TOTALA effekt (inklusive stressvägen).

    python planering.py data/hrv.db normal,tung,tung,normal,lugn,lugn,normal [startdatum]
"""
import datetime as dt
import itertools
import math
import sqlite3

import numpy as np

import modell

# Percentil av egen övrig stress senaste året. "mycket tung" behövs för stressiga perioder, som
# annars slår i taket vid p85.
NIVAER = {"lugn": 20, "normal": 50, "tung": 85, "mycket tung": 95}
VECKODAGAR = ["mån", "tis", "ons", "tor", "fre", "lör", "sön"]
LAS = ["Fritt", "Vila", "Pass", "Hårt pass", "Inget hårt pass"]
MINNE_HL = 7          # stressminnets halveringstid (dygn)
BAND_SD = 0.5         # normalspannets undre gräns: 60-nättersmedel − 0,5 sd
FONSTER_VECKOR = 8     # så många veckor bakåt definierar "den vanliga veckan"

_erf = np.vectorize(math.erf)


def phi(x):
    return 0.5 * (1 + _erf(np.asarray(x, float) / math.sqrt(2)))


def _o(s):
    return modell.ordn(s)


def _d(o):
    return modell.iso(o)


class Planerare:
    def __init__(self, con: sqlite3.Connection, R: dict):
        if not R.get("ok"):
            raise ValueError(R.get("skal", "Modellen kan inte räknas."))
        D, A = modell.las(con)
        self.D, self.R = D, R
        self.slut = max(D)                                   # sista dygnet med data
        self.load = {d: sum((a["load"] or 0) for a in acts) for d, acts in A.items()}
        self._stressdelning()
        self._planmodell()
        self._normalspann()
        self._vanlig_vecka(A)
        self._passbank(A)

    # ------------------------------------------------------------ skattningar
    def _stressdelning(self):
        """a1, a2: hur mycket dagens och gårdagens last höjer dagsstressen. Övrig stress = resten."""
        R, D = self.R, self.D
        ds = [d for d in sorted(D) if d >= R["start"] and D[d]["stress_avg"] is not None and D[d]["stress_avg"] >= 0]
        X = np.array([[1.0, self.load.get(d, 0.0), self.load.get(_d(_o(d) - 1), 0.0)] for d in ds])
        y = np.array([D[d]["stress_avg"] for d in ds], float)
        b, se, _ = modell.ols(X, y)
        self.a1, self.a2 = float(b[1]), float(b[2])
        self.ovr = {d: float(D[d]["stress_avg"] - self.a1 * X[i, 1] - self.a2 * X[i, 2]) for i, d in enumerate(ds)}
        sjuk = set(R["sjuk"])
        senaste = [v for d, v in self.ovr.items() if d not in sjuk and _o(d) > _o(self.slut) - 365]
        if len(senaste) < 60:
            senaste = [v for d, v in self.ovr.items() if d not in sjuk]
        self.niva = {k: float(np.percentile(senaste, p)) for k, p in NIVAER.items()}
        self.med = float(np.median(list(self.ovr.values())))
        self.ovr_ord = {_o(d): v for d, v in self.ovr.items()}
        # Historik för eftersläpningen: bara dygn som hämtades efter att de tog slut (dagens
        # pågående dygn har ett ofullständigt stressnitt).
        self.ovr_klar = {_o(d): v for d, v in self.ovr.items()
                         if (self.D[d].get("fetched_at") or "")[:10] > d or self.D[d].get("fetched_at") == "demo"}
        self._stresstroghet()

    def _stresstroghet(self):
        """Hur mycket av gårdagens och veckans överskott av övrig stress som hänger kvar i dagens.
        Skattas per person."""
        X, Y = [], []
        for x, v in self.ovr_ord.items():
            prev = [self.ovr_ord.get(x - k) for k in range(1, 8)]
            if any(p is None for p in prev):
                continue
            X.append([1.0, prev[0] - self.med, np.mean(prev[1:]) - self.med])
            Y.append(v - self.med)
        b, se, _ = modell.ols(np.array(X), np.array(Y))
        self.tr1, self.tr2 = float(b[1]), float(b[2])
        self.tr_se = (float(se[1]), float(se[2]))

    def _minne(self, x_ord: int, serie: dict) -> float:
        """Stressminne: EWMA (halveringstid MINNE_HL) av övrig stress minus median, dygn d−2 och bakåt.
        Gårdagen ligger redan i modellen som egen term."""
        lam = 0.5 ** (1 / MINNE_HL)
        num = den = 0.0
        for k in range(2, 22):
            v = serie.get(x_ord - k)
            if v is None:
                continue
            w = lam ** (k - 2)
            num += w * (v - self.med)
            den += w
        return num / den if den else 0.0

    def _planmodell(self):
        rows, form = self.R["rows"], self.R["form"]
        X, y = [], []
        for r, f in zip(rows, form):
            o = self.ovr.get(r["date"])
            if o is None:
                continue
            po = self.ovr.get(_d(r["ord"] - 1), o)
            X.append([1.0, r["h7"], o, po, r["load"], r["pload"], r["hour"], r["smin"], r["sjuk"], f,
                      self._minne(r["ord"], self.ovr_ord)])
            y.append(r["y"])
        X, y = np.array(X), np.array(y)
        b, se, n = modell.ols(X, y)
        names = ["konst", "h7", "ovr", "povr", "load", "pload", "hour", "smin", "sjuk", "form", "minne"]
        self.b = dict(zip(names, map(float, b)))
        self.se = dict(zip(names, map(float, se)))
        res = y - X @ b
        self.sigma = float(np.std(res, ddof=X.shape[1]))
        self.n_plan = n
        self._lastkostnad(X, res, names)
        # Ingen extra nivåjustering mot de senaste nätternas miss: formen bär redan nivån.
        # Timme för pass: median av dagens sista start på passdagar (rows har 12.0 på vilodagar)
        hrs = [r["hour"] for r in rows if r["load"] > 0]
        self.timme = float(np.median(hrs)) if hrs else 17.0

    def _lastkostnad(self, X, res, names):
        """Punkt 7: vad ett pass kostar natt 1 (dagens last) och natt 2 (gårdagens last), och summan,
        med osäkerhet. Summans standardfel tar hänsyn till att de två koefficienterna samvarierar.

        Spärren slår till när ingen av de tre är skild från noll (alla |t| < 2). Då finns inget
        underlag för att placera passen, och planeraren ska säga det i stället för att rekommendera.
        Bara natt 1 räcker inte som test: hos en del (cc98ea91 i första omgången) är natt 1 noll men
        natt 2 tydlig, och då är placeringen meningsfull."""
        ok = np.isfinite(X).all(1)
        Xo = X[ok]
        s2 = float(res @ res / (len(res) - X.shape[1]))
        V = s2 * np.linalg.pinv(Xo.T @ Xo)
        i1, i2 = names.index("load"), names.index("pload")
        b1, b2 = self.b["load"], self.b["pload"]
        se1, se2 = float(np.sqrt(V[i1, i1])), float(np.sqrt(V[i2, i2]))
        se_s = float(np.sqrt(V[i1, i1] + V[i2, i2] + 2 * V[i1, i2]))
        rad = lambda b, se: {"b": b, "se": se, "t": b / se if se > 0 else 0.0}
        self.lastkostnad = {"natt1": rad(b1, se1), "natt2": rad(b2, se2), "summa": rad(b1 + b2, se_s)}
        self.sparr = all(abs(v["t"]) < 2 for v in self.lastkostnad.values())

    def _normalspann(self):
        natter = [(d, v["hrv_night"]) for d, v in self.D.items() if v["hrv_night"] is not None]
        natter.sort()
        senaste = np.array([v for _, v in natter[-60:]], float)
        self.bas = float(senaste.mean())
        self.bas_sd = float(senaste.std(ddof=1))
        self.grans = self.bas - BAND_SD * self.bas_sd

    def _vanlig_vecka(self, A):
        """Den typiska veckan ur de senaste åtta veckorna: last, passdagar, hårda pass, styrka."""
        end = _o(self.slut)
        uth = {}      # uthållighetslast per dygn (allt utom styrka)
        styrka = {}   # (last, minuter) per styrkedygn
        for d, acts in A.items():
            for a in acts:
                if a["type"] in modell.STR:
                    l0, m0 = styrka.get(d, (0.0, 0.0))
                    styrka[d] = (l0 + (a["load"] or 0), m0 + (a["dur"] or 0) / 60)
                else:
                    uth[d] = uth.get(d, 0.0) + (a["load"] or 0)
        ar = [v for d, v in uth.items() if v > 0 and _o(d) > end - 365]
        self.hard_grans = float(np.percentile(ar, 75)) if len(ar) >= 8 else 150.0
        hard = [v for v in ar if v >= self.hard_grans]
        self.hard_last = float(np.median(hard)) if hard else self.hard_grans
        st = [v for d, v in styrka.items() if _o(d) > end - 365]
        self.styrka_last = float(np.median([s[0] for s in st])) if st else 20.0
        self.styrka_min = float(np.median([s[1] for s in st])) if st else 45.0

        veckor = []
        dag_last = np.zeros(7)
        styrkedag = np.zeros(7)
        for w in range(FONSTER_VECKOR):
            dagar = [_d(end - 7 * w - k) for k in range(7)]
            lv = [uth.get(x, 0.0) for x in dagar]
            veckor.append({"last": sum(lv), "dagar": sum(v > 0 for v in lv),
                           "hard": sum(v >= self.hard_grans for v in lv),
                           "styrka": sum(x in styrka for x in dagar)})
            for x, v in zip(dagar, lv):
                wd = dt.date.fromisoformat(x).weekday()
                dag_last[wd] += v / FONSTER_VECKOR
                styrkedag[wd] += (x in styrka) / FONSTER_VECKOR
        med = lambda k: float(np.median([v[k] for v in veckor]))
        self.vanlig = {"last": med("last"), "dagar": int(round(med("dagar"))), "hard": int(round(med("hard"))),
                       "styrka": int(round(med("styrka"))), "veckor": veckor}
        self.veckodagslast = dag_last
        self.styrkemonster = styrkedag

    def _passbank(self, A):
        """Last per minut per aktivitetsgrupp och passklass, för att översätta last till minuter."""
        end = _o(self.slut)
        per = {}
        for d, acts in A.items():
            if _o(d) <= end - 365:
                continue
            for a in acts:
                if a["type"] in modell.STR or not a["load"] or not a["dur"] or a["dur"] < 600:
                    continue
                g = modell.grupp(a["type"])
                klass = "hard" if a["load"] >= self.hard_grans else "lugn"
                per.setdefault(g, {"min": 0.0, "hard": [], "lugn": []})
                per[g]["min"] += a["dur"] / 60
                per[g][klass].append(a["load"] / (a["dur"] / 60))
        self.passbank = {g: v for g, v in per.items() if len(v["hard"]) + len(v["lugn"]) >= 5}

    def nulage(self, n: int = 7) -> dict:
        """De senaste n fullständiga dygnens övriga stress och närmaste nivå, plus de senaste nätterna.
        Ett dygn räknas som fullständigt om det hämtades minst två dygn efter att det tog slut."""
        def klart(d):
            f = self.D[d].get("fetched_at")
            if f == "demo" or not f:
                return f == "demo"
            return f[:10] >= _d(_o(d) + 2)
        dagar = [d for d in sorted(self.ovr) if klart(d)][-n:]
        if not dagar:
            return {"niva": "normal", "medel": None, "dagar": [], "natter": []}
        medel = float(np.mean([self.ovr[d] for d in dagar]))
        niva = min(self.niva, key=lambda k: abs(self.niva[k] - medel))
        natter = sorted((d, v["hrv_night"]) for d, v in self.D.items() if v["hrv_night"] is not None)[-n:]
        return {"niva": niva, "medel": medel, "dagar": dagar, "natter": natter}

    # ------------------------------------------------------------ framskrivning
    def _form_vid(self, x_ord: int) -> float:
        """Formen vid dygn x: EWMA av tidigare residualer, framtida residualer = 0 (väntevärdet)."""
        if not hasattr(self, "_res"):
            self._res = {_o(d): float(r) for d, r in zip(self.R["dates"], self.R["res"])}
            self._res_slut = max(self._res)
            self._lam = 0.5 ** (1.0 / self.R["hl"])
        num = den = 0.0
        for k in range(1, 61):
            o = x_ord - k
            w = self._lam ** k
            if o in self._res:
                num += w * self._res[o]
                den += w
            elif o > self._res_slut:
                den += w
        return num / den if den > 0 else 0.0

    def forutsag(self, start: dt.date, nivaer: list[str], L: np.ndarray, smin: np.ndarray) -> np.ndarray:
        """Förväntad HRV för nätterna efter dag 0–6. L och smin har formen (n, 7): last och
        styrkeminuter per dag i n kandidatveckor."""
        b = self.b
        n = L.shape[0]
        s = start.toordinal()
        # Kända nätter de sju dygnen före start (natten mot datumet). Okända fylls med basnivån.
        natt = np.empty((n, 14))
        for k in range(7):
            v = self.D.get(_d(s - 6 + k), {}).get("hrv_night")
            natt[:, k] = v if v is not None else self.bas
        forre = _d(s - 1)
        ovr, povr, minne = self.stressbana(start, nivaer)
        if forre in self.D or _o(forre) <= _o(self.slut):
            pL = self.load.get(forre, 0.0)
        else:
            pL = self.vanlig["last"] / 7
        Y = np.empty((n, 7))
        for i in range(7):
            h7 = natt[:, i:i + 7].mean(1)
            hour = np.where(L[:, i] > 0, self.timme, 12.0)
            prevL = L[:, i - 1] if i else np.full(n, pL)
            y = (b["konst"] + b["h7"] * h7 + b["ovr"] * ovr[i] + b["povr"] * (ovr[i - 1] if i else povr)
                 + b["load"] * L[:, i] + b["pload"] * prevL + b["hour"] * hour + b["smin"] * smin[:, i]
                 + b["form"] * self._form_vid(s + i) + b["minne"] * minne[i])
            Y[:, i] = y
            natt[:, i + 7] = y            # natten efter dag i = natten mot dag i+1
        return Y

    def stressbana(self, start: dt.date, nivaer: list[str]):
        """Väntad övrig stress dag 0–6 med eftersläpning, gårdagens stress före dag 0, och stressminnet.

        Nivån användaren anger beskriver dagen i sig. Ovanpå den ligger det överskott som hänger
        kvar från dagarna före: överskott(d) = tr1·överskott(d−1) + tr2·snitt(överskott d−2..d−7),
        där överskottet för historiska dygn är uppmätt övrig stress minus median och för planerade
        dygn det som ärvts (inte den angivna nivån – annars skulle en tung vecka skena)."""
        s = start.toordinal()
        overskott = {o: v - self.med for o, v in self.ovr_klar.items() if o < s}
        serie = {o: v for o, v in self.ovr_klar.items() if o < s}
        ovr = []
        for i in range(7):
            o = s + i
            prev = [overskott.get(o - k, 0.0) for k in range(1, 8)]
            arv = self.tr1 * prev[0] + self.tr2 * float(np.mean(prev[1:]))
            v = self.niva[nivaer[i]] + arv
            overskott[o] = arv
            serie[o] = v
            ovr.append(v)
        povr = serie.get(s - 1, self.med)
        minne = [self._minne(s + i, serie) for i in range(7)]
        return ovr, povr, minne

    def poang(self, Y):
        p = phi((self.grans - Y) / self.sigma)
        return p.sum(1), p

    # ------------------------------------------------------------ förslag
    def foreslag(self, start: dt.date, nivaer: list[str], veckolast: float | None = None,
                 dagar: int | None = None, hard: int | None = None, styrka: int | None = None,
                 las: list[str] | None = None, styrkedagar: list[int] | None = None,
                 max_vila_i_rad: int | None = 2) -> dict:
        """max_vila_i_rad: konsekvensregel. Modellen ser inget värde i regelbundenhet (bara kostnader),
        så utan regeln samlar den gärna alla pass på veckans lugna dagar och lämnar långa vilosträckor."""
        v = self.vanlig
        veckolast = v["last"] if veckolast is None else veckolast
        dagar = v["dagar"] if dagar is None else dagar
        hard = min(v["hard"] if hard is None else hard, dagar)
        styrka = v["styrka"] if styrka is None else styrka
        las = las or ["Fritt"] * 7
        dagar = max(dagar, sum(x in ("Pass", "Hårt pass") for x in las))

        H = self.hard_last
        if hard and hard * H > veckolast:
            H = veckolast / max(dagar, 1)
        M = (veckolast - hard * H) / (dagar - hard) if dagar > hard else 0.0
        M = max(M, 0.0)
        multi = [H] * hard + [M] * (dagar - hard) + [0.0] * (7 - dagar)
        perms = []
        for p in set(itertools.permutations(multi)):
            ok = True
            for x, l in zip(p, las):
                if (l == "Vila" and x > 0) or (l == "Pass" and x == 0) or \
                   (l == "Hårt pass" and not (hard and x == H)) or (l == "Inget hårt pass" and hard and x == H and H != M):
                    ok = False
                    break
            if ok and max_vila_i_rad is not None and dagar > 0:
                run = longest = 0
                for x in p:
                    run = run + 1 if x == 0 else 0
                    longest = max(longest, run)
                ok = longest <= max_vila_i_rad
            if ok:
                perms.append(p)
        if not perms:
            return {"ok": False, "skal": "Inget upplägg uppfyller låsningarna och regeln om vilodagar i rad. "
                                         "Lätta på någon låsning, ändra antalet passdagar eller tillåt fler "
                                         "vilodagar i rad."}
        tillatna = [i for i in range(7) if las[i] != "Vila"]
        if styrkedagar:
            kombos = [tuple(sorted(styrkedagar))]
        else:
            k = min(styrka, len(tillatna))
            kombos = list(itertools.combinations(tillatna, k))
        Lend = np.array([p for p in perms for _ in kombos], float)
        S = np.zeros_like(Lend)
        for j, kombo in enumerate(kombos * len(perms)):
            S[j, list(kombo)] = 1.0
        Ltot = Lend + S * self.styrka_last
        Y = self.forutsag(start, nivaer, Ltot, S * self.styrka_min)
        under, p = self.poang(Y)
        order = np.lexsort((-Y.min(1), under))
        best, worst = order[0], order[-1]

        # Referens 1: personens vanliga veckodagsmönster, skalat till samma veckolast
        wd = [(start + dt.timedelta(days=i)).weekday() for i in range(7)]
        mon = self.veckodagslast[wd]
        mon = mon * (veckolast / mon.sum()) if mon.sum() > 0 else np.full(7, veckolast / 7)
        sm = np.zeros(7)
        if styrka:
            sm[np.argsort(-self.styrkemonster[wd])[:styrka]] = 1.0
        Yv = self.forutsag(start, nivaer, (mon + sm * self.styrka_last)[None], (sm * self.styrka_min)[None])
        # Referens 2: samma vecka utan träning (vad stressen ensam ger)
        Y0 = self.forutsag(start, nivaer, np.zeros((1, 7)), np.zeros((1, 7)))

        stress_vantad = self.stressbana(start, nivaer)[0]

        def plan(i):
            dagar_ut = []
            for k in range(7):
                datum = start + dt.timedelta(days=k)
                lend, st = float(Lend[i, k]), bool(S[i, k])
                roll = "Vila" if lend == 0 else ("Hårt" if hard and lend == H and H != M else "Lugnt/medel")
                text = self.passtext(lend, roll == "Hårt")
                if st:
                    s_txt = f"styrka ~{5 * round(self.styrka_min / 5):.0f} min"
                    text = s_txt.capitalize() if lend == 0 else f"{s_txt.capitalize()} först, sedan {text[0].lower()}{text[1:]}"
                dagar_ut.append({"datum": datum.isoformat(), "veckodag": VECKODAGAR[datum.weekday()],
                                 "stress": nivaer[k], "stress_vantad": float(stress_vantad[k]), "roll": roll, "last": lend, "styrka": st,
                                 "forslag": text,
                                 "hrv": float(Y[i, k]), "p_under": float(p[i, k])})
            return {"dagar": dagar_ut, "under": float(under[i]), "samsta": float(Y[i].min())}

        # Spärr (punkt 7): ingen rangordning när lastkostnaden inte är skild från noll någon av nätterna.
        # Veckan med vanligt mönster och veckan utan träning visas ändå, de bygger på stressen.
        return {"ok": True, "sparr": self.sparr, "lastkostnad": self.lastkostnad, "stress_vantad": stress_vantad,
                "plan": None if self.sparr else plan(best), "samsta_upplagg": None if self.sparr else plan(worst),
                "vanlig_under": float(self.poang(Yv)[0][0]), "vanlig_hrv": Yv[0].tolist(),
                "utan_traning_under": float(self.poang(Y0)[0][0]), "utan_traning_hrv": Y0[0].tolist(),
                "n_kandidater": int(len(Lend)), "veckolast": veckolast, "dagar": dagar, "hard": hard,
                "styrka": styrka, "H": H, "M": M, "grans": self.grans, "bas": self.bas, "sigma": self.sigma}

    def passtext(self, last: float, hard: bool) -> str:
        if last <= 0:
            return "Vila eller promenad"
        klass = "hard" if hard else "lugn"
        alt = []
        for g, v in sorted(self.passbank.items(), key=lambda kv: -kv[1]["min"])[:2]:
            rates = v[klass] if len(v[klass]) >= 3 else v["hard"] + v["lugn"]
            minuter = last / float(np.median(rates))
            alt.append(f"{modell_gruppnamn(g)} ~{5 * round(minuter / 5):.0f} min")
        return " eller ".join(alt) if alt else f"Pass med last ~{last:.0f}"


GRUPPNAMN = {"styrka": "styrka", "inomhuscykel": "inomhuscykel", "cykel": "cykel", "lopning": "löpning",
             "vandring": "vandring", "skidor": "skidor/skate", "paddling": "paddling", "ovrigt": "övrigt"}


def modell_gruppnamn(g):
    return GRUPPNAMN.get(g, g)


if __name__ == "__main__":
    import sys
    con = sqlite3.connect(sys.argv[1] if len(sys.argv) > 1 else "data/hrv.db")
    niv = (sys.argv[2] if len(sys.argv) > 2 else "normal,normal,normal,normal,normal,lugn,lugn").split(",")
    R = modell.kor(con)
    P = Planerare(con, R)
    start = dt.date.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else \
        dt.date.fromisoformat(P.slut) + dt.timedelta(days=1)
    print(f"Planeringsmodell (n {P.n_plan}, sigma {P.sigma:.1f} ms):")
    for k in ("ovr", "povr", "minne", "load", "pload", "hour", "smin", "h7", "form"):
        print(f"  {k:6s} {P.b[k]:+.4f}  t {P.b[k] / P.se[k]:+.1f}")
    print(f"Stressens tröghet: gårdagens överskott {P.tr1:+.2f}, veckans {P.tr2:+.2f}")
    print(f"Stressdelning: a1 {P.a1:+.4f}  a2 {P.a2:+.4f}   nivåer {', '.join(f'{k} {v:.1f}' for k, v in P.niva.items())}")
    print(f"Normalspann: bas {P.bas:.1f}, sd {P.bas_sd:.1f}, gräns {P.grans:.1f}")
    print(f"Vanlig vecka: {P.vanlig['last']:.0f} last, {P.vanlig['dagar']} passdagar, {P.vanlig['hard']} hårda, "
          f"{P.vanlig['styrka']} styrka   (hårt ≥ {P.hard_grans:.0f}, typiskt hårt {P.hard_last:.0f})")
    F = P.foreslag(start, niv)
    if not F["ok"]:
        sys.exit(F["skal"])
    lk = P.lastkostnad
    print("Lastkostnad per 100: " + ", ".join(f"{k} {v['b'] * 100:+.2f} ms (t {v['t']:+.1f})" for k, v in lk.items())
          + f" | spärr: {'JA' if P.sparr else 'nej'}")
    if F["sparr"]:
        print("Underlaget för tunt: inget förslag. Vanligt mönster:", [round(x, 1) for x in F["vanlig_hrv"]])
        sys.exit(0)
    print(f"\nVecka från {start} ({F['n_kandidater']} kandidater):")
    for d in F["plan"]["dagar"]:
        print(f"  {d['veckodag']} {d['datum']}  {d['stress']:6s} väntad {d['stress_vantad']:4.1f}  {d['roll']:11s} {d['last']:5.0f}"
              f"{' +styrka' if d['styrka'] else '        '}  HRV {d['hrv']:5.1f}  P(under) {d['p_under']:.2f}   {d['forslag']}")
    print(f"Förväntade nätter under normalspannet: förslag {F['plan']['under']:.2f}, vanligt mönster "
          f"{F['vanlig_under']:.2f}, sämsta upplägg {F['samsta_upplagg']['under']:.2f}, utan träning "
          f"{F['utan_traning_under']:.2f}")
    print(f"Sämsta natt: förslag {F['plan']['samsta']:.1f}, sämsta upplägg {F['samsta_upplagg']['samsta']:.1f}")
