"""Vilopulsvägen (punkt 10b): klocka utan HRV-status, modellen räknas på vilopulsen.

1. En planterad lasteffekt på vilopulsen (+0,02 slag/min per lastenhet natten efter) ska återfinnas.
2. Utan planterad effekt ska ingen falsk lasteffekt dyka upp (|t| < 2,5).
3. Med HRV väljs HRV-vägen, och bidraget märks med rätt utfall.
4. Bidraget från vilopulsvägen märks 'vilopuls' (samla.py poolar aldrig det med HRV)."""
import os
import sys
import tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="hrvdata_vp_"))
os.environ["HRVDATA_DIR"] = str(tmp)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bidrag
import db
import demo_data
import modell

SANT = 0.02
fel = []


def kontroll(namn, ok, txt):
    print(("ok  " if ok else "FEL ") + f" {namn}: {txt}")
    if not ok:
        fel.append(namn)


def kor(namn, **kw):
    con = db.connect(tmp / f"{namn}.db")
    demo_data.create(con, n_days=900, seed=7, **kw)
    return modell.kor(con)


# 1. planterad effekt
R = kor("planterad", utan_hrv=True, vilopuls_last=SANT)
kontroll("vilopulsvägen vald", R.get("ok") and R.get("utfall") == "vilopuls" and R.get("enhet") == "slag/min",
         f"utfall {R.get('utfall')}, enhet {R.get('enhet')}")
k = R["koef_total"]["load"]
t = k["b"] / k["se"]
kontroll("planterad lasteffekt återfunnen", abs(k["b"] - SANT) < 3 * k["se"] and t > 2,
         f"{k['b']:+.4f} ± {k['se']:.4f} slag/min per enhet (sant {SANT:+.4f}), t {t:+.1f}")
kontroll("tungt pass höjer vilopulsen", R["tungt_pass"] is None or R["tungt_pass"]["natt1"] > 0,
         f"natt 1 efter tungt pass {R['tungt_pass']['natt1']:+.2f} slag/min" if R["tungt_pass"] else "inget tungt pass")

# 2. ingen falsk effekt
R0 = kor("utan", utan_hrv=True, vilopuls_last=0.0)
k0 = R0["koef_total"]["load"]
t0 = k0["b"] / k0["se"]
kontroll("ingen falsk lasteffekt", abs(t0) < 2.5, f"{k0['b']:+.4f} ± {k0['se']:.4f}, t {t0:+.1f}")

# 3. med HRV väljs HRV
Rh = kor("hrv")
kontroll("HRV-vägen när HRV finns", Rh.get("ok") and Rh.get("utfall") == "hrv", f"utfall {Rh.get('utfall')}")

# 4. bidragets märkning
b_vp = bidrag.bygg(R, "prov", {}, kalla="garmin")
b_h = bidrag.bygg(Rh, "prov", {}, kalla="garmin")
kontroll("bidraget märks", b_vp["beskrivning"].get("utfall") == "vilopuls" and b_h["beskrivning"].get("utfall") == "hrv",
         f"vilopuls → {b_vp['beskrivning'].get('utfall')}, hrv → {b_h['beskrivning'].get('utfall')}")

print("ALLT OK" if not fel else f"FEL: {fel}")
sys.exit(1 if fel else 0)
