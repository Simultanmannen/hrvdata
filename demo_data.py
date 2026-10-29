"""Påhittad person med känd dos–respons, för att prova appen utan Garmin-konto.

Sanningen som simuleras (modellen ska hitta ungefär detta):
  HRV(natt d+1) = 58 + form(d) − 0,045·last(d) − 1,1·(stress(d) − 28) − 5·sjuk + brus(7 ms)
  form = AR(1) med φ = 0,93 och sd ≈ 4,5 ms
  stress(d) = 28 + AR-brus + 0,015·last(d) − 0,008·last(d−1)
Allt sparas med fetched_at = 'demo' och pass-id 'demo-…', så det kan tas bort igen.

Vilopulsvägen (punkt 10b) provas med create(con, utan_hrv=True, vilopuls_last=...): en klocka utan
HRV-status, där vilopulsen natten efter dygn d får + vilopuls_last·last(d) slag/min. Utan argumenten
blir datan exakt som förut (samma slumptal).

    python demo_data.py           # skapa
    python demo_data.py --remove  # ta bort
"""
import sys
from datetime import date, timedelta

import numpy as np

import db

TYPER = [("indoor_cycling", 0.25, 60, 95), ("running", 0.25, 50, 110), ("strength_training", 0.15, 45, 35),
         ("kayaking_v2", 0.2, 75, 120), ("hiking", 0.1, 150, 140), ("cycling", 0.05, 120, 200)]


def create(con, n_days: int = 900, seed: int = 7, utan_hrv: bool = False, vilopuls_last: float = 0.0) -> None:
    rng = np.random.default_rng(seed)
    today = date.today()
    days = [today - timedelta(days=n_days - i) for i in range(n_days + 1)]
    N = len(days)

    form = np.zeros(N)
    for i in range(1, N):
        form[i] = 0.93 * form[i - 1] + rng.normal(0, 4.5 * np.sqrt(1 - 0.93 ** 2))
    sick = np.zeros(N, bool)
    for s in rng.choice(np.arange(30, N - 10), size=6, replace=False):
        sick[s:s + rng.integers(3, 7)] = True

    acts, load = [], np.zeros(N)
    names, probs = [t[0] for t in TYPER], np.array([t[1] for t in TYPER])
    for i, d in enumerate(days):
        if sick[i] or rng.random() > 0.62:
            continue
        k = rng.choice(len(TYPER), p=probs / probs.sum())
        typ, _, minutes, l_mean = TYPER[k]
        dur = max(20, rng.normal(minutes, minutes * 0.3))
        tl = max(5, rng.lognormal(np.log(l_mean), 0.45))
        hour = rng.choice([7, 12, 17, 18, 19])
        load[i] += tl
        acts.append({"activity_id": f"demo-{i}", "date": d.isoformat(), "type_key": typ, "name": f"Demo {typ}",
                     "start_local": f"{d.isoformat()} {hour:02d}:{rng.integers(0, 60):02d}:00",
                     "duration_s": dur * 60, "distance_m": None, "avg_hr": None, "max_hr": None,
                     "aerobic_te": round(min(5, 1.017 * np.log(tl) - 1.433), 1), "anaerobic_te": 0.5,
                     "training_load": round(tl), "calories": None})

    ar = np.zeros(N)
    for i in range(1, N):
        ar[i] = 0.5 * ar[i - 1] + rng.normal(0, 5)
    stress = 28 + ar + 0.015 * load - 0.008 * np.r_[0, load[:-1]] + 6 * sick
    stress = np.clip(np.round(stress), 5, 80)

    hrv = np.full(N, np.nan)
    for i in range(N - 1):
        hrv[i + 1] = 58 + form[i] - 0.045 * load[i] - 1.1 * (stress[i] - 28) - 5 * sick[i] + rng.normal(0, 7)
    hrv = np.clip(np.round(hrv), 20, 140)

    rows = []
    for i, d in enumerate(days):
        prev = hrv[max(0, i - 6):i + 1]
        prev = prev[np.isfinite(prev)]
        rows.append({
            "date": d.isoformat(),
            "hrv_night": None if utan_hrv else (float(hrv[i]) if np.isfinite(hrv[i]) else None),
            "hrv_7d": None if utan_hrv else (float(round(prev.mean())) if len(prev) else None),
            "hrv_status": None if utan_hrv else "BALANCED",
            "rhr": float(round(48 + 4 * sick[i] - 0.1 * form[i] + rng.normal(0, 1.2)
                               + vilopuls_last * (load[i - 1] if i else 0.0))),
            "respiration_avg": float(round(14 + 1.3 * sick[i] + rng.normal(0, 0.3), 1)),
            "skin_temp_dev_c": float(round(0.5 * sick[i] + rng.normal(0, 0.2), 1)),
            "stress_avg": float(stress[i]),
            "sleep_seconds": float(rng.normal(7.4, 0.6) * 3600),
            "sleep_score": float(np.clip(round(rng.normal(75, 9)), 30, 100)),
            "deep_seconds": None, "rem_seconds": None, "awake_seconds": None,
            "steps": float(max(1500, round(rng.normal(9000, 2500)))),
            "fetched_at": "demo",
        })
    with con:
        for r in rows:
            db._upsert(con, "days", db.DAY_COLS, r)
        for a in acts:
            db._upsert(con, "activities", db.ACT_COLS, a)


def remove(con) -> None:
    import sync
    sync.remove_demo(con)


if __name__ == "__main__":
    c = db.connect()
    if "--remove" in sys.argv:
        remove(c)
        print("Exempeldata borttagen.")
    else:
        create(c)
        print("Exempeldata skapad.")
