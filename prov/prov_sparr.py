"""Planerarspärren (punkt 7): exempelpersonen utan träningseffekt ska spärras, en med effekt bara natt 2
ska inte spärras. Exempelpersonens sanning: total effekt natt 1 ≈ −0,0615 per enhet, natt 2 via
nedvarvningen ≈ +0,0088. Testet tar bort dem och lägger ev. till en egen natt 2-effekt."""
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="hrvdata_sparr_"))
os.environ["HRVDATA_DIR"] = str(tmp)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db
import demo_data
import modell
import planering

fel = []


def person(namn, natt2):
    c = db.connect(tmp / f"{namn}.db")
    demo_data.create(c, n_days=1000, seed=7)
    D, A = modell.las(c)
    last = {d: sum((a["load"] or 0) for a in acts) for d, acts in A.items()}
    with c:
        for d in D:
            o = modell.ordn(d)
            l1, l2 = last.get(modell.iso(o - 1), 0.0), last.get(modell.iso(o - 2), 0.0)
            c.execute("update days set hrv_night = hrv_night + ? where date=? and hrv_night is not null",
                      (0.0615 * l1 - 0.0088 * l2 + natt2 * l2, d))
    R = modell.kor(c)
    P = planering.Planerare(c, R)
    F = P.foreslag(dt.date.fromisoformat(P.slut) + dt.timedelta(days=1), ["normal"] * 7)
    return P, F


for namn, natt2, vantat in (("utan_effekt", 0.0, True), ("bara_natt2", 0.02, False)):
    P, F = person(namn, natt2)
    lk = P.lastkostnad
    ok = P.sparr == vantat and (F["plan"] is None) == vantat and F["sparr"] == vantat
    print(("ok   " if ok else "FEL  ") + f"{namn}: spärr {P.sparr} (väntat {vantat}) | "
          + ", ".join(f"{k} t {v['t']:+.1f}" for k, v in lk.items()))
    if not ok:
        fel.append(namn)

print("ALLT OK" if not fel else f"{len(fel)} FEL: {fel}")
sys.exit(1 if fel else 0)
