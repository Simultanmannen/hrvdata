"""Gruppstöd och schema 3 (punkt 11).

1. Bidraget har schema 3 med 'bred' och 'bred_komplett'. Delblocket för BASE + form i 'bred' ger exakt
   koef_direkt, och 'direkt'/'total' är kvar.
2. En person utan träningseffekt (som i prov_sparr) har spärren, och får lastkoefficienten krympt mot
   gruppens: närmare priorn än den egna skattningen, och med mindre osäkerhet.
3. En prior med enorm spridning ändrar ingenting (krympt = egen OLS).
4. Ingen krympning när priorn gäller en annan källa, vilopuls, eller färre än tre personer.
5. Labbet använder de krympta koefficienterna när de ges."""
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

tmp = Path(tempfile.mkdtemp(prefix="hrvdata_grupp_"))
os.environ["HRVDATA_DIR"] = str(tmp)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bidrag
import db
import demo_data
import labb
import modell
import passtyper
import planering

fel = []
PRIOR_KOL = ["konst", "h7", "stress", "hour", "smin", "pstress", "pload", "load", "sjuk"]


def kontroll(namn, ok, txt):
    print(("ok  " if ok else "FEL ") + f" {namn}: {txt}")
    if not ok:
        fel.append(namn)


def prior(beta_load, tau_load, k=6, kalla="garmin", utfall="hrv", stor=False):
    beta = [50.0, 0.3, -1.1, 0.0, 0.0, 0.1, 0.0, beta_load, -3.0]
    tau = [30.0, 0.2, 0.5, 0.5, 0.05, 0.3, 0.02, tau_load, 3.0]
    if stor:
        tau = [1e6] * len(beta)
    return {"schema": 1, "namn": PRIOR_KOL, "beta": beta, "G": np.diag(np.square(tau)).tolist(),
            "typ": "diagonal G (REML)", "kalla": kalla, "utfall": utfall, "k": k, "datum": "2026-10-29"}


# Person utan lasteffekt (samma konstruktion som prov_sparr)
c = db.connect(tmp / "utan.db")
demo_data.create(c, n_days=1000, seed=7)
D, A = modell.las(c)
last = {d: sum((a["load"] or 0) for a in acts) for d, acts in A.items()}
with c:
    for d in D:
        o = modell.ordn(d)
        l1, l2 = last.get(modell.iso(o - 1), 0.0), last.get(modell.iso(o - 2), 0.0)
        c.execute("update days set hrv_night = hrv_night + ? where date=? and hrv_night is not null",
                  (0.0615 * l1 - 0.0088 * l2, d))
R = modell.kor(c)

# 1. schema 3
b = bidrag.bygg(R, "prov", {}, kalla="garmin")
tr = b["tillracklig"]
kontroll("schema 3", b["schema"] == 3 and {"direkt", "total", "bred", "bred_komplett"} <= set(tr),
         f"schema {b['schema']}, block {sorted(tr)}")
B = tr["bred"]
kontroll("bred har facit-ordningen", B["namn"][:9] == PRIOR_KOL and B["namn"][-1] == "form"
         and B["namn"][9:-1] == modell.BRED_EXTRA, ", ".join(B["namn"]))
idx = [B["namn"].index(k) for k in tr["direkt"]["namn"]]
bb = np.linalg.solve(np.array(B["ra"]["XtX"])[np.ix_(idx, idx)], np.array(B["ra"]["Xty"])[idx])
diff = max(abs(bb[i + 1] - R["koef"][k]["b"]) for i, k in enumerate(tr["direkt"]["namn"][1:]))
kontroll("delblocket ger koef_direkt", diff < 1e-8, f"största skillnad {diff:.1e}")

# 2. krympning
egen = R["koef"]["load"]
P = planering.Planerare(c, R)
lk = P.lastkostnad
kontroll("spärren slår till (då används stödet)", P.sparr,
         f"t natt 1 {lk['natt1']['t']:+.1f}, natt 2 {lk['natt2']['t']:+.1f}, summa {lk['summa']['t']:+.1f}")
S = modell.gruppkrympning(R, prior(-0.05, 0.01), "garmin")
k = S["koef"]["load"]
kontroll("krympt mot gruppen", abs(k["b"] - (-0.05)) < abs(egen["b"] - (-0.05)) and k["se"] < egen["se"],
         f"egen {egen['b']:+.4f} ± {egen['se']:.4f}, krympt {k['b']:+.4f} ± {k['se']:.4f}, grupp −0,0500")
kontroll("egna skattningen återges", abs(S["egen"]["load"]["b"] - egen["b"]) < 1e-10, "")

# 3. stor spridning ändrar inget
S0 = modell.gruppkrympning(R, prior(-0.05, 0.01, stor=True), "garmin")
d0 = max(abs(S0["koef"][x]["b"] - R["koef"][x]["b"]) for x in R["koef"])
kontroll("vag prior = egen OLS", d0 < 1e-4, f"största skillnad {d0:.1e}")

# 4. när det inte ska krympas
kontroll("annan källa", modell.gruppkrympning(R, prior(-0.05, 0.01, kalla="oura"), "garmin") is None, "")
kontroll("för få personer", modell.gruppkrympning(R, prior(-0.05, 0.01, k=2), "garmin") is None, "")
kontroll("ingen prior", modell.gruppkrympning(R, None, "garmin") is None, "")
cv = db.connect(tmp / "vp.db")
demo_data.create(cv, n_days=900, seed=7, utan_hrv=True, vilopuls_last=0.02)
Rv = modell.kor(cv)
kontroll("vilopuls", modell.gruppkrympning(Rv, prior(-0.05, 0.01), "garmin") is None, f"utfall {Rv.get('utfall')}")

# 5. labbet
PT = passtyper.bygg(c, R["sjuk"])
L0, L1 = labb.Labb(R, P, PT), labb.Labb(R, P, PT, stod=S)
j = L0.i["load"]
kontroll("labbet använder stödet", abs(L1.b[j] - k["b"]) < 1e-12 and abs(L0.b[j] - egen["b"]) < 1e-10,
         f"utan {L0.b[j]:+.4f}, med {L1.b[j]:+.4f}")
hard = {"typ": "Egen last", "last": 250, "timme": 17}
vila = {"typ": "Vila"}
mall = [hard, vila, hard, vila, hard, vila, vila]
s0, s1 = L0.kor(mall, 4, ["medel"] * 4), L1.kor(mall, 4, ["medel"] * 4)
kontroll("hårda veckan kostar mer med stödet", s1["snitt"] < s0["snitt"], f"{s0['snitt']:+.2f} → {s1['snitt']:+.2f} ms")

print("ALLT OK" if not fel else f"FEL: {fel}")
sys.exit(1 if fel else 0)
