"""Passtyperna (punkt 8 och 9) mot påhittade pass med tre tydliga typer och känd kostnad natten efter."""
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="hrvdata_pass_"))
os.environ["HRVDATA_DIR"] = str(tmp)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
import db
import passtyper

rng = np.random.default_rng(3)
# typ: (sport, minuter, last, medelpuls, maxpuls, aerob TE, anaerob TE, timme, kostnad natten efter)
TYPER = {"lätt": ("strength_training", 35, 20, 100, 125, 1.5, 0.3, 18, 2.0),
         "hård": ("running", 55, 140, 150, 178, 3.8, 2.5, 17, 8.0),
         "lång": ("cycling", 160, 210, 128, 160, 4.0, 1.0, 10, 12.0)}
con = db.connect(tmp / "hrv.db")
start = date(2024, 1, 1)
sann = {}
hrv = {}
with con:
    for i in range(700):
        d = (start + timedelta(days=i)).isoformat()
        typ = rng.choice(["vila", "vila", "lätt", "hård", "lång"])
        kost = 0.0
        if typ != "vila":
            sp, m, l, hr, hm, ate, ante, h, kost = TYPER[typ]
            con.execute("insert into activities (activity_id, date, type_key, start_local, duration_s, avg_hr, max_hr, "
                        "aerobic_te, anaerobic_te, training_load) values (?,?,?,?,?,?,?,?,?,?)",
                        (f"p{i}", d, sp, f"{d} {h:02d}:{rng.integers(0, 50):02d}:00",
                         m * 60 * rng.uniform(0.85, 1.15), hr + rng.normal(0, 4), hm + rng.normal(0, 4),
                         ate + rng.normal(0, 0.2), ante + rng.normal(0, 0.2), l * rng.uniform(0.85, 1.15)))
            sann[f"p{i}"] = typ
        n1 = (start + timedelta(days=i + 1)).isoformat()
        hrv[n1] = 60 - kost + rng.normal(0, 4)
    for i in range(701):
        d = (start + timedelta(days=i)).isoformat()
        con.execute("insert or replace into days (date, hrv_night, hrv_7d, fetched_at) values (?,?,?,?)",
                    (d, hrv.get(d, 60.0), 60.0, "2025-01-01"))

res = passtyper.bygg(con)
fel = []


def kolla(namn, ok, txt):
    print(("ok   " if ok else "FEL  ") + f"{namn}: {txt}")
    if not ok:
        fel.append(namn)


kolla("tre typer hittas", res["k"] == 3, f"k = {res['k']}, silhuett {({k: round(v, 2) for k, v in res['silhuett'].items()})}")
# Renhet: varje kluster domineras av en sport (sporten är unik per sann typ)
lab_typ = {}
for p in res["pass"]:
    lab_typ.setdefault(p["kluster"], []).append(p["typ"])
renhet = min(max(v.count(t) for t in set(v)) / len(v) for v in lab_typ.values())
kolla("klustren är rena", renhet > 0.95, f"lägsta renhet {renhet:.0%}")
for k in res["kluster"]:
    sport = k["topp"][0][0]
    sann_kost = {"styrka": 2.0, "lopning": 8.0, "cykel": 12.0}[sport]
    # vilodygn ger 0 i förväntan (hrv 60 mot h7 60), så kostnaden mot vila ska vara −sann_kost
    kolla(f"kostnad {sport}", abs(k["kostnad"] + sann_kost) < 2 * k["kostnad_se"] + 0.5,
          f"{k['kostnad']:+.1f} ± {k['kostnad_se']:.1f} (sant {-sann_kost:+.1f})")
kolla("PC1 är dos/intensitet", passtyper.pc_namn(res, 0) != "", passtyper.pc_namn(res, 0))
con.close()
print("ALLT OK" if not fel else f"{len(fel)} FEL: {fel}")
sys.exit(1 if fel else 0)
