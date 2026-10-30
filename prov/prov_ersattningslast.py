"""Ersättningslast för klockor utan Garmins träningslast (punkt 15b).

1. En kopia av en riktig databas (data/hrv.db, annars ../garmin.db) med training_load nollad: lasten ersätts
   ur aerob TE, korrelerar starkt med den riktiga (r > 0,8 per dygn) och lastkoefficienten får samma tecken.
2. Samma kopia utan TE också: pulsvägen. Svagare samband (rapporteras), men samma tecken.
3. Originaldatabasen ersätts inte (Garmins last finns), så modellen är oförändrad.
4. Exempelpersonen utan last (demo_data utan_last=True): pulsvägen, bidraget märks garmin_puls, och
   lastkoefficienten har rätt tecken (sanningen är negativ).
Delarna 1–3 hoppas över om ingen riktig databas finns."""
import os
import shutil
import sqlite3
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np

tmp = Path(tempfile.mkdtemp(prefix="hrvdata_last_"))
os.environ["HRVDATA_DIR"] = str(tmp)
APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))
import bidrag
import db
import demo_data
import modell

fel = []


def kontroll(namn, ok, txt):
    print(("ok  " if ok else "FEL ") + f" {namn}: {txt}")
    if not ok:
        fel.append(namn)


def dagslast(A):
    d = defaultdict(float)
    for dag, acts in A.items():
        d[dag] = sum((a["load"] or 0) for a in acts)
    return d


kalla_db = next((p for p in (APP / "data" / "hrv.db", APP.parent / "garmin.db") if p.exists()), None)
if kalla_db is None:
    print("hoppar över 1–3: ingen riktig databas")
else:
    orig = sqlite3.connect(kalla_db)
    D0, A0 = modell.las(orig)
    R0 = modell.kor(orig)
    kontroll("originalet ersätts inte", modell.lastvag(A0) is None and R0.get("last_ersatt") is None,
             f"{kalla_db.name}: Garmins last används")
    riktig = dagslast(A0)
    for vag, sql in (("te", "update activities set training_load = null"),
                     ("puls", "update activities set training_load = null, aerobic_te = null, anaerobic_te = null")):
        kopia = tmp / f"kopia_{vag}.db"
        shutil.copy(kalla_db, kopia)
        c = sqlite3.connect(kopia)
        c.execute(sql)
        c.commit()
        D, A = modell.las(c)
        kontroll(f"{vag}: rätt väg vald", modell.lastvag(A) == vag, f"väg {modell.lastvag(A)}")
        ers = dagslast(A)
        dagar = [d for d in riktig if riktig[d] > 0 and d in ers]
        r = float(np.corrcoef([riktig[d] for d in dagar], [ers[d] for d in dagar])[0, 1])
        krav = 0.8 if vag == "te" else 0.5
        kontroll(f"{vag}: samband med Garmins last", r > krav, f"r {r:.2f} per passdygn (krav > {krav}), "
                                                            f"{len(dagar)} dygn")
        R = modell.kor(c)
        k0, k = R0["koef_total"]["load"], R["koef_total"]["load"]
        kontroll(f"{vag}: lastkoefficienten samma tecken", np.sign(k["b"]) == np.sign(k0["b"]),
                 f"Garmins last {k0['b'] * 100:+.2f} ms per 100 (t {k0['b'] / k0['se']:+.1f}), ersättning "
                 f"{k['b'] * 100:+.2f} (t {k['b'] / k['se']:+.1f})")
        kontroll(f"{vag}: bidraget märks", bidrag.bygg(R, "prov", {}, kalla="garmin")["beskrivning"]["kalla"]
                 == f"garmin_{vag}", "")

# 4. exempelpersonen utan last
c = db.connect(tmp / "demo.db")
demo_data.create(c, n_days=900, seed=7, utan_last=True)
R = modell.kor(c)
kontroll("demo: pulsvägen", R.get("last_ersatt") == "puls", f"väg {R.get('last_ersatt')}")
k = R["koef_total"]["load"]
kontroll("demo: lasten kostar", k["b"] < 0 and k["b"] / k["se"] < -2,
         f"{k['b'] * 100:+.2f} ms per 100 i ersättningslast, t {k['b'] / k['se']:+.1f}")
b = bidrag.bygg(R, "prov", {}, kalla="garmin")
kontroll("demo: bidraget märks garmin_puls", b["beskrivning"]["kalla"] == "garmin_puls", b["beskrivning"]["kalla"])
kontroll("demo: ingen gruppkrympning mot Garmins last",
         modell.gruppkrympning(R, {"utfall": "hrv", "kalla": "garmin", "k": 6, "namn": [], "beta": [], "G": []},
                               modell.effektiv_kalla(R, "garmin")) is None, "")
d0 = db.connect(tmp / "demo_vanlig.db")
demo_data.create(d0, n_days=900, seed=7)
kontroll("demo med last oförändrad", modell.kor(d0).get("last_ersatt") is None, "")

print("ALLT OK" if not fel else f"FEL: {fel}")
sys.exit(1 if fel else 0)
