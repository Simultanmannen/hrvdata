"""Bidragsfilen: aggregerade resultat från modellen, utan dygnsvärden och utan datum.

Innehållet är det som behövs för att pröva hur allmängiltig dos–responsmodellen är:
koefficienter med standardfel, prognosförmåga, den långsamma formens egenskaper och
några modellfria mått. Användaren ser hela filen innan den sparas eller skickas."""
import json
import math
from datetime import date

import numpy as np

import modell
from version import VERSION

SCHEMA = 1


def _r(x, n=4):
    """Avrunda rekursivt; NaN/inf blir None så att filen är giltig JSON."""
    if isinstance(x, dict):
        return {str(k): _r(v, n) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_r(v, n) for v in x]
    if isinstance(x, (bool, str)) or x is None:
        return x
    if isinstance(x, (int, np.integer)):
        return int(x)
    try:
        f = float(x)
    except (TypeError, ValueError):
        return x
    return round(f, n) if math.isfinite(f) else None


def bygg(R: dict, deltagare: str, bakgrund: dict | None = None, kalla: str | None = None) -> dict:
    if not R.get("ok"):
        raise ValueError("Modellen har inte kunnat köras.")
    beskrivning = dict(R["beskrivning"])
    beskrivning["kalla"] = kalla or "garmin"   # garmin: EPOC-last + 0–100-stress. oura: min×intensitet + minuter hög stress
    return _r({
        "schema": SCHEMA,
        "app_version": VERSION,
        "modellversion": modell.MODELLVERSION,
        "deltagare": deltagare,
        "skapad": date.today().isoformat(),
        "bakgrund": {k: v for k, v in (bakgrund or {}).items() if v not in (None, "", "Vill inte ange")},
        "data": {"n_rader": R["n"], "t0": R["t0"], "n_utvardering": R["n_utv"], "n_ols": R["n_ols"]},
        "beskrivning": beskrivning,
        "r2": R["r2"],
        "mae": R["mae"],
        "halveringstid": R["hl"],
        "halveringstid_skanning": R["scan"],
        "koef_direkt": R["koef"],
        "koef_total": R["koef_total"],
        "form": {"sd": R["form_sd"], "p10": R["form_p10"], "p90": R["form_p90"],
                 "acf": R["form_acf"], "manad": R["form_manad"]},
        "residual_acf1": R["res_acf1"],
        "tungt_pass": R["tungt_pass"],
        "lastklasser": R["lastklasser"],
        "nedvarvning": R["nedvarvning"],
    })


def text(b: dict) -> str:
    return json.dumps(b, ensure_ascii=False, indent=1)


def filnamn(b: dict) -> str:
    return f"hrv-bidrag-{b['deltagare']}-{b['skapad']}.json"
