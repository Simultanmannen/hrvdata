"""Hämtar pass och dygnsvärden från Garmin Connect till den lokala databasen.

Bara det som HRV-modellen behöver hämtas: passens sammanfattning (typ, längd, last, starttid)
och per dygn nattlig HRV, vilopuls, andning, hudtemperatur, sömn, stress och steg.
Inga GPS-spår eller sekunddata.

    python sync.py login
    python sync.py sync --days 1095
"""
import argparse
import getpass
import json
import sys
import time
from datetime import date, datetime, timedelta

import db
from config import JSON_DIR, TOKEN_DIR, ensure_dirs

PAUSE_S = 0.3        # var snäll mot Garmins API
UTAN_HRV_STOPP = 120 # så många dygn i rad utan HRV bakåt i tiden = klockan mätte inte HRV längre bak
log = print          # dashboarden byter ut den här för att visa förloppet


def dig(d, *path):
    for p in path:
        if d is None:
            return None
        if isinstance(p, int):
            d = d[p] if isinstance(d, list) and len(d) > p else None
        else:
            d = d.get(p) if isinstance(d, dict) else None
    return d


# ---------------------------------------------------------------- inloggning
def garmin_client(interactive: bool = True):
    from garminconnect import Garmin

    ensure_dirs()
    try:
        api = Garmin()
        api.login(str(TOKEN_DIR))
        return api
    except Exception as e:
        if not interactive:
            raise
        log(f"Sparad inloggning saknas eller har gått ut ({type(e).__name__}). Logga in:")
    email = input("Garmin-e-post: ").strip()
    password = getpass.getpass("Lösenord (visas inte): ")
    api = Garmin(email=email, password=password,
                 prompt_mfa=lambda: input("Engångskod (MFA) från Garmin: ").strip())
    api.login(str(TOKEN_DIR))
    log(f"Inloggad. Token sparad i {TOKEN_DIR} (lösenordet sparas inte).")
    return api


def is_logged_in() -> bool:
    return TOKEN_DIR.exists() and any(TOKEN_DIR.iterdir())


def start_login(email: str, password: str):
    """Returnerar (api, state) om Garmin kräver engångskod, annars (None, None) och token är sparad."""
    from garminconnect import Garmin

    ensure_dirs()
    api = Garmin(email=email, password=password, return_on_mfa=True)
    status, state = api.login()
    if status == "needs_mfa":
        return api, state
    api.client.dump(str(TOKEN_DIR))
    return None, None


def finish_login(api, state, code: str) -> None:
    api.resume_login(state, code)
    api.client.dump(str(TOKEN_DIR))


def logout() -> None:
    import shutil
    shutil.rmtree(TOKEN_DIR, ignore_errors=True)


def friendly_error(e: Exception) -> str:
    name, text = type(e).__name__, str(e).lower()
    if "TooManyRequests" in name or "429" in text:
        return "Garmin säger att det har varit för många försök. Vänta 10–15 minuter och försök igen."
    if "Authentication" in name or "401" in text or "unauthorized" in text:
        return "Inloggningen misslyckades. Kontrollera e-post och lösenord (samma som i Garmin Connect-appen)."
    if "Connection" in name or "timeout" in text or "resolve" in text:
        return "Kunde inte nå Garmin. Kontrollera internetanslutningen och försök igen."
    return f"Något gick fel ({name}). Försök igen om en stund."


def _with_retry(fn, *args):
    """Garmin begränsar antalet anrop. Vid 'för många anrop' väntar vi och försöker igen."""
    for attempt in range(4):
        try:
            return fn(*args)
        except Exception as e:
            if attempt < 3 and ("TooManyRequests" in type(e).__name__ or "429" in str(e)):
                wait = 60 * (attempt + 1)
                log(f"Garmin vill att vi saktar ner – väntar {wait} s …")
                time.sleep(wait)
                continue
            raise


# ---------------------------------------------------------------- pass
def activity_row(g: dict) -> dict:
    start = g.get("startTimeLocal") or ""
    return {
        "activity_id": str(g["activityId"]),
        "date": start[:10] or None,
        "type_key": dig(g, "activityType", "typeKey"),
        "name": g.get("activityName"),
        "start_local": start or None,
        "duration_s": g.get("duration"),
        "distance_m": g.get("distance"),
        "avg_hr": g.get("averageHR"),
        "max_hr": g.get("maxHR"),
        "aerobic_te": g.get("aerobicTrainingEffect"),
        "anaerobic_te": g.get("anaerobicTrainingEffect"),
        "training_load": g.get("activityTrainingLoad"),
        "calories": g.get("calories"),
    }


def sync_activities(api, con, since: str | None, full: bool, progress=None) -> int:
    """Listar pass hos Garmin (100 åt gången) och sparar sammanfattningen. Utan full stannar
    listningen vid första redan kända pass."""
    known = db.known_activity_ids(con)
    since_d = since or "0000"
    new, start, page = 0, 0, 100
    while True:
        batch = _with_retry(api.get_activities, start, page)
        if not batch:
            break
        rows, stop = [], False
        for g in batch:
            day = (g.get("startTimeLocal") or "")[:10]
            if day and day < since_d:
                stop = True
                break
            aid = str(g["activityId"])
            if aid in known and not full:
                stop = True
                break
            if aid not in known:
                new += 1
            rows.append(activity_row(g))
        db.save_activities(con, rows)
        if progress:
            progress("list", new, None)
        if stop or len(batch) < page:
            break
        start += page
        time.sleep(PAUSE_S)
    log(f"{new} nya pass.")
    return new


# ---------------------------------------------------------------- dygnsvärden
def day_row(d: str, raw: dict, fetched_at: str) -> dict:
    hrv = dig(raw, "hrv", "hrvSummary") or {}
    dto = dig(raw, "sleep", "dailySleepDTO") or {}
    summ = raw.get("summary") or {}
    stress = summ.get("averageStressLevel")
    return {
        "date": d,
        "hrv_night": hrv.get("lastNightAvg"),
        "hrv_7d": hrv.get("weeklyAvg"),
        "hrv_status": hrv.get("status"),
        "rhr": dig(raw, "sleep", "restingHeartRate") or summ.get("restingHeartRate"),
        "respiration_avg": dto.get("averageRespirationValue"),
        "skin_temp_dev_c": dig(raw, "sleep", "avgSkinTempDeviationC"),
        "stress_avg": stress if stress is not None and stress >= 0 else None,  # -1/-2 = ej mätt
        "sleep_seconds": dto.get("sleepTimeSeconds"),
        "sleep_score": dig(dto, "sleepScores", "overall", "value"),
        "deep_seconds": dto.get("deepSleepSeconds"),
        "rem_seconds": dto.get("remSleepSeconds"),
        "awake_seconds": dto.get("awakeSleepSeconds"),
        "steps": summ.get("totalSteps"),
        "fetched_at": fetched_at,
    }


def complete(d: str, fetched_at: str | None) -> bool:
    """Garmin fyller på sömn, HRV och stress i efterhand. Ett dygn räknas som klart först om
    det hämtades minst två dygn efter att det tog slut."""
    if not fetched_at or fetched_at == "demo":
        return False
    return fetched_at[:10] >= (date.fromisoformat(d) + timedelta(days=2)).isoformat()


def sync_days(api, con, days_back: int, progress=None) -> int:
    """Hämtar dygnsvärden från idag och bakåt. Klara dygn hoppas över, så en avbruten hämtning
    fortsätter där den slutade. Stannar när klockan inte har mätt HRV på UTAN_HRV_STOPP dygn i rad."""
    status = db.day_status(con)
    today = date.today()
    fetched, utan_hrv = 0, 0
    for i in range(days_back):
        d = (today - timedelta(days=i)).isoformat()
        if progress:
            progress("days", i + 1, days_back)
        if d in status and complete(d, status[d][0]):
            har_hrv = status[d][1]
        else:
            raw = {}
            for key, fn in (("summary", api.get_user_summary), ("sleep", api.get_sleep_data),
                            ("hrv", api.get_hrv_data)):
                try:
                    raw[key] = _with_retry(fn, d)
                except Exception as e:
                    if "TooManyRequests" in type(e).__name__ or "Connection" in type(e).__name__:
                        raise
                    raw[key] = None
                time.sleep(PAUSE_S)
            now = datetime.now().isoformat(timespec="seconds")
            (JSON_DIR / f"{d}.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            row = day_row(d, raw, now)
            db.save_day(con, row)
            har_hrv = row["hrv_night"] is not None
            fetched += 1
        utan_hrv = 0 if har_hrv else utan_hrv + 1
        if utan_hrv >= UTAN_HRV_STOPP and i >= UTAN_HRV_STOPP:
            log(f"Ingen HRV på {UTAN_HRV_STOPP} dygn i rad före {d} – klockan mätte inte HRV längre bak. Stannar.")
            break
    log(f"{fetched} dygn hämtade.")
    return fetched


def remove_demo(con) -> None:
    with con:
        con.execute("DELETE FROM activities WHERE activity_id LIKE 'demo-%'")
        con.execute("DELETE FROM days WHERE fetched_at='demo'")


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description="Hämta Garmin-data till lokal databas")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="Logga in och spara token")
    p = sub.add_parser("sync", help="Hämta pass och dygnsvärden")
    p.add_argument("--days", type=int, default=30, help="Antal dygn bakåt (default 30)")
    p.add_argument("--full", action="store_true", help="Gå igenom hela passlistan")
    sub.add_parser("demo", help="Skapa påhittad exempeldata")
    args = ap.parse_args(argv)

    ensure_dirs()
    con = db.connect()
    if args.cmd == "login":
        garmin_client()
    elif args.cmd == "sync":
        api = garmin_client()
        remove_demo(con)
        since = (date.today() - timedelta(days=args.days)).isoformat()
        sync_activities(api, con, since, args.full)
        sync_days(api, con, args.days)
    elif args.cmd == "demo":
        import demo_data
        demo_data.create(con)
    return 0


if __name__ == "__main__":
    sys.exit(main())
