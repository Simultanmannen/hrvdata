"""Träningslabbet (punkt 12) på exempelpersonen, vars sanning är känd (last −0,045 ms/enhet direkt,
stress −1,1 ms/enhet, se demo_data.py).

1. Historiken uppspelad dag för dag (samma mönster som historiken) ger samma medelnatt som historiken.
2. Alla indata på sitt historiska medel ger avvikelsen noll varje natt (jämvikten är rätt räknad).
3. Ett extra pass på 200 i last kostar natten efter lika mycket som modellens totala lasteffekt.
4. Rekursionen: efter tre hårda dagar i rad ligger vilonätterna lägre än i samma plan utan h7-termen
   (kräver att h7-koefficienten är positiv, som hos alla hittills). Veckosnittet beror inte på ordningen.
5. Fitness: konstant last L i ett år närmar sig L. Veckomallen och texten går att köra.
6. p10 som sannolikhet per natt (punkt 14a): senaste året uppspelat ger ungefär 10 % under p10."""
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

tmp = Path(tempfile.mkdtemp(prefix="hrvdata_labb_"))
os.environ["HRVDATA_DIR"] = str(tmp)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db
import demo_data
import labb
import modell
import passtyper
import planering

fel = []


def kontroll(namn, ok, txt):
    print(("ok  " if ok else "FEL ") + f" {namn}: {txt}")
    if not ok:
        fel.append(namn)


con = db.connect(tmp / "demo.db")
demo_data.create(con, n_days=900, seed=7)
R = modell.kor(con)
P = planering.Planerare(con, R)
PT = passtyper.bygg(con, R["sjuk"])
L = labb.Labb(R, P, PT)

# 1. historiken uppspelad
rows = R["rows"]
col = lambda k: np.array([r[k] for r in rows], float)
X = {k: col(k) for k in ("load", "pload", "stress", "pstress", "hour", "smin", "sjuk")}
y = col("y")
Y = L.simulera(X, start_avv=y[:7] - L.jamvikt[0])[0] + L.jamvikt[0]
kontroll("historiken uppspelad", abs(Y.mean() - y.mean()) < 1.0,
         f"simulerat medel {Y.mean():.2f} ms, faktiskt {y.mean():.2f} ms")

# 2. allt på medel
N = 56
M = {k: np.full(N, L.medel[k]) for k in ("load", "pload", "stress", "pstress", "hour", "smin", "sjuk")}
Y0 = L.simulera(M)[0]
kontroll("medel ger noll", np.abs(Y0).max() < 1e-6, f"största avvikelse {np.abs(Y0).max():.2e} ms")

# 3. ett extra pass på 200
M1 = {k: v.copy() for k, v in M.items()}
t = 20
M1["load"][t] += 200
M1["pload"][t + 1] += 200
M1["stress"][t] += L.a1 * 200
M1["stress"][t + 1] += L.a2 * 200
M1["pstress"][t + 1] += L.a1 * 200
M1["pstress"][t + 2] += L.a2 * 200
d = L.simulera(M1)[0]
tot = R["koef_total"]["load"]
kontroll("kostnad natten efter", abs(d[t] - 200 * tot["b"]) < 2 * 200 * tot["se"] + 0.5,
         f"simulerat {d[t]:+.2f} ms, modellens totala lasteffekt {200 * tot['b']:+.2f} ± {2 * 200 * tot['se']:.2f} ms "
         f"(sant ≈ {-0.0615 * 200:+.1f})")

# 4. rekursionen
hard = {"typ": "Egen last", "last": 250, "timme": 17}
vila = {"typ": "Vila"}
iklump = L.kor([hard, hard, hard, vila, vila, vila, vila], 4, ["medel"] * 4)
utspritt = L.kor([hard, vila, vila, hard, vila, hard, vila], 4, ["medel"] * 4)
b_h7 = L.b[L.i[L.niva]]
kontroll("h7-koefficienten positiv", b_h7 > 0, f"{b_h7:.3f}")
# Samma plan utan rekursionen (h7-termen noll): vilonätterna efter klumpen ska ligga högre då
utan = labb.Labb(R, P, PT)
utan.drag[:, utan.i[utan.niva]] = 0.0
utan.jamvikt = utan._jamvikt(utan.drag)
ej_rek = utan.kor([hard, hard, hard, vila, vila, vila, vila], 4, ["medel"] * 4)
kontroll("tre hårda i rad ackumuleras", iklump["medel"][4:7].mean() < ej_rek["medel"][4:7].mean() - 0.3,
         f"vilonätterna efter klumpen {iklump['medel'][4:7].mean():+.1f} ms med rekursion, "
         f"{ej_rek['medel'][4:7].mean():+.1f} ms utan")
kontroll("samma veckosnitt", abs(iklump["snitt"] - utspritt["snitt"]) < 0.3,
         f"{iklump['snitt']:+.2f} mot {utspritt['snitt']:+.2f} ms (modellen är linjär)")

# 5. fitness och mall
f = L.fitness(np.full(365, 80.0))
kontroll("fitness närmar sig lasten", abs(f[-1] - 80) < 1, f"efter ett år {f[-1]:.1f} (last 80)")
S = L.kor(L.mall_vanlig, 6, ["medel", "tung", "tung", "medel", "lugn", "medel"], "Som du brukar", sjuk_vecka=2)
vilomall = [vila] * 7
frisk, sjuk = L.kor(vilomall, 4, ["medel"] * 4), L.kor(vilomall, 4, ["medel"] * 4, sjuk_vecka=2)
kontroll("sjukperioden kostar", sjuk["medel"][14:18].mean() < frisk["medel"][14:18].mean() - 1,
         f"vilovecka med sjukdagar {sjuk['medel'][14:18].mean():+.1f}, utan {frisk['medel'][14:18].mean():+.1f} ms")
kontroll("bandet omsluter punkten", bool(((S["lo"] <= S["medel"] + 1e-9) & (S["medel"] <= S["hi"] + 1e-9)).all()), "")
# 6. p10 som sannolikhet per natt (punkt 14a): historiens senaste år uppspelat ska ge ungefär 10 % av
# nätterna under p10, och ungefär lika många som faktiskt hamnade där
mu = L.simulera(X, start_avv=y[:7] - L.jamvikt[0])[0][-365:] + L.jamvikt[0] - y[-365:].mean()
andel = float(L.p_under(mu).mean())
faktisk = float(np.mean(y[-365:] - y[-365:].mean() < L.p10))
kontroll("andel under p10", 0.06 < andel < 0.14 and abs(andel - faktisk) < 0.04,
         f"förutsagt {andel * 100:.1f} %, faktiskt {faktisk * 100:.1f} % (σ_res {L.sigma_res:.1f} ms)")
kontroll("nattbandet bredare än kostnadsbandet", bool((S["natt_hi"] - S["natt_lo"] > S["hi"] - S["lo"]).all()),
         f"{(S['natt_hi'] - S['natt_lo']).mean():.1f} mot {(S['hi'] - S['lo']).mean():.1f} ms")
print("   mall:", [m["typ"] for m in L.mall_vanlig])
print("   ", L.text(S))

print("ALLT OK" if not fel else f"FEL: {fel}")
sys.exit(1 if fel else 0)
