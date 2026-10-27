"""Stressen under vaken tid (sync.vaket_stress) mot påhittade dygn med känt svar, plus migreringen av
gamla databaser och att en fil utan stressdata aldrig skriver över ett uträknat värde."""
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="hrvdata_vaken_"))
os.environ["HRVDATA_DIR"] = str(tmp)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db
import sync

STEG = 180_000  # 3 minuter i ms
MIDNATT = int(datetime(2026, 9, 29, tzinfo=timezone.utc).timestamp() * 1000)


def ms(h, m=0):
    return MIDNATT + (h * 60 + m) * 60_000


def dygn(vaken_varde=40, somn_varde=10, uppvak=(6, 30), somnar=(23, 0), aktivitet=((17, 0), (18, 0))):
    """Ett kalenderdygn med 3-minutersvärden: sömnvärde före uppvaknandet och efter insomnandet,
    vaket värde däremellan, −2 under passet och några −1."""
    arr = []
    for i in range(480):
        t = MIDNATT + i * STEG
        if t < ms(*uppvak) or t >= ms(*somnar):
            v = somn_varde
        elif ms(*aktivitet[0]) <= t < ms(*aktivitet[1]):
            v = -2
        else:
            v = vaken_varde
        arr.append([t, v])
    arr[200][1] = -1
    stress = {"stressValuesArray": arr, "endTimestampGMT": "2026-09-30T00:00:00.0"}
    return stress, {"sleepEndTimestampGMT": ms(*uppvak)}, {"sleepStartTimestampGMT": ms(*somnar)}


fel = []
def kolla(namn, varde, vantat):
    ok = varde == vantat if not isinstance(vantat, float) else abs(varde - vantat) < 1e-9
    print(("ok   " if ok else "FEL  ") + f"{namn}: {varde} (väntat {vantat})")
    if not ok:
        fel.append(namn)


# 1. Vanligt dygn: 06:30–23:00 = 330 värden, minus 20 under passet och ett −1
s, somn, nasta = dygn()
m, minuter = sync.vaket_stress(s, somn, nasta)
kolla("medel vaken tid", m, 40.0)
kolla("mätbara minuter", minuter, 3.0 * (330 - 20 - 1))
# 2. Ingen natt efter: inget värde (att räkna till midnatt tog med kvällens sömn och drog ner medlet)
kolla("utan nästa natts insomning inget värde", sync.vaket_stress(s, somn, None), (None, None))
# 3. Insomning efter midnatt: avkortas vid dygnets slut
s3, somn3, _ = dygn(somnar=(23, 59))
m3, min3 = sync.vaket_stress(s3, somn3, {"sleepStartTimestampGMT": ms(24, 45)})
# 06:30–24:00 = 350 värden, minus 20 under passet och ett −1. Insomningen 00:45 ligger utanför arrayen.
kolla("insomning efter midnatt avkortas vid midnatt", min3, 3.0 * (350 - 20 - 1))
kolla("  och medlet är bara vakna värden", m3, 40.0)
# 4. För lite vaken tid
s4, somn4, nasta4 = dygn(uppvak=(21, 0), somnar=(22, 30), aktivitet=((0, 0), (0, 0)))
kolla("under 2 timmar ger inget värde", sync.vaket_stress(s4, somn4, nasta4), (None, None))
# 5. Saknad sömnpost
kolla("utan sömnpost inget värde", sync.vaket_stress(s, None, nasta), (None, None))

# 6. Migrering: en gammal databas utan kolumnerna får dem
gammal = tmp / "gammal.db"
c = sqlite3.connect(gammal)
c.execute("CREATE TABLE days (date TEXT PRIMARY KEY, hrv_night REAL, hrv_7d REAL, stress_avg REAL, fetched_at TEXT)")
c.execute("INSERT INTO days VALUES ('2026-09-29', 60, 58, 28, '2026-10-05')")
c.commit(); c.close()
con = db.connect(gammal)
cols = {r[1] for r in con.execute("PRAGMA table_info(days)")}
kolla("migrering lägger till kolumnerna", {"stress_vaken", "stress_vaken_min"} <= cols, True)
kolla("befintlig rad orörd", con.execute("select hrv_night from days").fetchone()[0], 60.0)

# 7. uppdatera_vaken: räknar ur filer, och en fil utan stress skriver inte över
mapp = tmp / "filer"; mapp.mkdir()
(mapp / "2026-09-29.json").write_text(json.dumps({"stress": s, "sleep": {"dailySleepDTO": somn}}), encoding="utf-8")
(mapp / "2026-09-30.json").write_text(json.dumps({"sleep": {"dailySleepDTO": nasta}}), encoding="utf-8")
kolla("uppdatera_vaken räknar", sync.uppdatera_vaken(con, "2026-09-29", mapp), True)
kolla("värdet sparat", con.execute("select stress_vaken from days").fetchone()[0], 40.0)
annan = tmp / "annan"; annan.mkdir()
(annan / "2026-09-29.json").write_text(json.dumps({"summary": {}, "sleep": {"dailySleepDTO": somn}}), encoding="utf-8")
kolla("fil utan stress rör inget", sync.uppdatera_vaken(con, "2026-09-29", annan), False)
kolla("värdet kvar", con.execute("select stress_vaken from days").fetchone()[0], 40.0)
con.close()

# 8. Hela hämtningen mot ett låtsas-Garmin: nya dygn får måttet, äldre klara dygn utan stress kompletteras
from datetime import date, timedelta


class FejkGarmin:
    def __init__(self):
        self.anrop = []

    def _mid(self, d):
        return int(datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp() * 1000)

    def get_user_summary(self, d):
        self.anrop.append(("summary", d)); return {"averageStressLevel": 30, "totalSteps": 8000}

    def get_hrv_data(self, d):
        self.anrop.append(("hrv", d)); return {"hrvSummary": {"lastNightAvg": 60, "weeklyAvg": 58}}

    def get_sleep_data(self, d):
        self.anrop.append(("sleep", d)); m = self._mid(d)
        # natten som slutar 06:30 på morgonen d och började 23:00 kvällen före
        return {"dailySleepDTO": {"sleepStartTimestampGMT": m - 60 * 60_000, "sleepEndTimestampGMT": m + 390 * 60_000}}

    def get_stress_data(self, d):
        self.anrop.append(("stress", d)); m = self._mid(d)
        arr = [[m + i * STEG, 10 if (i * 3 < 390 or i * 3 >= 1380) else 35] for i in range(480)]
        return {"stressValuesArray": arr, "endTimestampGMT": (datetime.fromisoformat(d) + timedelta(days=1)).isoformat() + ".0"}


sync.PAUSE_S = 0.0
con = db.connect()
idag = date.today()
# Två gamla klara dygn 30 och 29 dagar bak, hämtade med en äldre version: JSON utan 'stress'
gammalt = (idag - timedelta(days=30)).isoformat()
for d_ in (gammalt, (idag - timedelta(days=29)).isoformat()):
    db.save_day(con, {"date": d_, "hrv_night": 61, "hrv_7d": 59, "stress_avg": 29,
                      "fetched_at": (idag - timedelta(days=20)).isoformat()})
    (sync.JSON_DIR / f"{d_}.json").write_text(json.dumps({"summary": {}, "sleep": FejkGarmin().get_sleep_data(d_)}),
                                              encoding="utf-8")
api = FejkGarmin()
sync.sync_days(api, con, days_back=5)
igar = (idag - timedelta(days=1)).isoformat()
v = con.execute("select stress_vaken, stress_vaken_min from days where date=?", (igar,)).fetchone()
kolla("nytt dygn (igår) får vaken stress 35", v[0], 35.0)
kolla("  på 06:30–23:00 = 990 minuter", v[1], 990.0)
kolla("gammalt dygn kompletterat med stress", ("stress", gammalt) in api.anrop, True)
kolla("  men sömnen hämtas inte i onödan", ("sleep", gammalt) in api.anrop, False)
kolla("  och får måttet", con.execute("select stress_vaken from days where date=?", (gammalt,)).fetchone()[0], 35.0)
api2 = FejkGarmin()
sync.komplettera_stress_fonster(api2, con)
kolla("andra körningen hämtar inget extra", [a for a in api2.anrop], [])
con.close()

print("ALLT OK" if not fel else f"{len(fel)} FEL: {fel}")
sys.exit(1 if fel else 0)
