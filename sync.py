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
from datetime import date, datetime, timedelta, timezone

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


MIN_VAKNA_VARDEN = 40   # under 2 timmar mätbar vaken tid räcker inte för ett dygnsmått
STRESS_DAGAR = 140      # Garmin servar 3-minutersstressen ungefär 4,5 månader bakåt. Klara dygn inom det
                        # fönstret som saknar den kompletteras, annars försvinner den för gott.


def _gmt_ms(s: str | None) -> float | None:
    """'2026-09-29T22:00:00.0' (GMT) → epoch-millisekunder."""
    if not s:
        return None
    return datetime.fromisoformat(s[:19]).replace(tzinfo=timezone.utc).timestamp() * 1000


def vaket_stress(stress: dict | None, somn: dict | None, nasta_somn: dict | None):
    """Garmins 3-minutersstress under vaken tid dygn d. Returnerar (medel, mätbara minuter) eller (None, None).

    Fönstret går från uppvaknandet (sömnposten för dygn d, natten som slutade på morgonen) till
    insomnandet (sömnposten för dygn d+1, som börjar kvällen d), avkortat vid midnatt eftersom
    stressarrayen bara täcker kalenderdygnet. Saknas nästa natts insomning blir det inget värde:
    att räkna till midnatt skulle ta med kvällens sömn och dra ner medlet. Dagens dygn får alltså
    sitt värde först när morgondagens sömn hämtats.
    Värden < 0 tas bort (−1 = för lite data, −2 = aktivitet), så passen ingår inte.

    Dagsmedlet (averageStressLevel) räknas däremot över hela kalenderdygnet inklusive sömnen.
    Det här måttet skiljer jobb- och livsstress från nattens nivå."""
    if not stress or not somn:
        return None, None
    arr = stress.get("stressValuesArray") or []
    vaken = somn.get("sleepEndTimestampGMT")
    if not arr or vaken is None:
        return None, None
    slut = _gmt_ms(stress.get("endTimestampGMT")) or (arr[-1][0] + 180_000)
    insomning = (nasta_somn or {}).get("sleepStartTimestampGMT")
    if insomning is None or insomning <= vaken:
        return None, None
    slut = min(slut, insomning)
    v = [x for t, x in arr if t is not None and x is not None and vaken <= t < slut and x >= 0]
    if len(v) < MIN_VAKNA_VARDEN:
        return None, None
    return sum(v) / len(v), 3.0 * len(v)


def _las_json(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def uppdatera_vaken(con, d: str, mapp=None) -> bool:
    """Räkna om stress_vaken för dygn d ur de sparade JSON-filerna för d och d+1."""
    mapp = mapp or JSON_DIR
    raw = _las_json(mapp / f"{d}.json")
    if not raw or not raw.get("stress") or not dig(raw, "sleep", "dailySleepDTO"):
        return False  # filen saknar underlag: rör inte ett värde som räknats ur en annan källa
    nxt = _las_json(mapp / f"{(date.fromisoformat(d) + timedelta(days=1)).isoformat()}.json") or {}
    medel, minuter = vaket_stress(raw.get("stress"), dig(raw, "sleep", "dailySleepDTO"),
                                  dig(nxt, "sleep", "dailySleepDTO"))
    with con:
        cur = con.execute("UPDATE days SET stress_vaken=?, stress_vaken_min=? WHERE date=?", (medel, minuter, d))
    return cur.rowcount > 0 and medel is not None


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
                            ("hrv", api.get_hrv_data), ("stress", api.get_stress_data)):
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
            # Vaken stress för d behöver insomnandet ur d+1, och d−1 får sitt insomnande ur d.
            # Hämtningen går bakåt i tiden, så d+1 ligger redan på disk när d hämtas.
            uppdatera_vaken(con, d)
            uppdatera_vaken(con, (date.fromisoformat(d) - timedelta(days=1)).isoformat())
            har_hrv = row["hrv_night"] is not None
            fetched += 1
        utan_hrv = 0 if har_hrv else utan_hrv + 1
        if utan_hrv >= UTAN_HRV_STOPP and i >= UTAN_HRV_STOPP:
            log(f"Ingen HRV på {UTAN_HRV_STOPP} dygn i rad före {d} – klockan mätte inte HRV längre bak. Stannar.")
            break
    log(f"{fetched} dygn hämtade.")
    extra = komplettera_stress_fonster(api, con, progress)
    if extra:
        log(f"Stress under dagen kompletterad för {extra} äldre dygn.")
    return fetched


def komplettera_stress_fonster(api, con, progress=None) -> int:
    """Gå igenom de senaste STRESS_DAGAR dygnen oavsett hur långt den vanliga hämtningen gick.
    Behövs första gången efter uppdateringen: äldre dygn räknas som klara och hämtas inte om."""
    status = db.day_status(con)
    today = date.today()
    n = 0
    for i in range(STRESS_DAGAR):
        d = (today - timedelta(days=i)).isoformat()
        if progress:
            progress("stress", i + 1, STRESS_DAGAR)
        if d in status and status[d][0] != "demo":
            n += komplettera_stress(api, con, d)
    return n


def komplettera_stress(api, con, d: str) -> int:
    """Ett klart dygn som saknar 3-minutersstressen (hämtat med en äldre version, eller inläst ur en
    garmin.db utan JSON) får den, och sömnposten om den också saknas. Returnerar 1 om något hämtades."""
    p = JSON_DIR / f"{d}.json"
    raw = _las_json(p) or {}
    saknas = [k for k in ("sleep", "stress") if k not in raw]
    if not saknas:
        return 0
    for key in saknas:
        fn = api.get_sleep_data if key == "sleep" else api.get_stress_data
        try:
            raw[key] = _with_retry(fn, d)
        except Exception as e:
            if "TooManyRequests" in type(e).__name__ or "Connection" in type(e).__name__:
                raise
            raw[key] = None
        time.sleep(PAUSE_S)
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    uppdatera_vaken(con, d)
    uppdatera_vaken(con, (date.fromisoformat(d) - timedelta(days=1)).isoformat())
    return 1


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
    p = sub.add_parser("vaken", help="Räkna om stressen under vaken tid ur sparade JSON-filer")
    p.add_argument("--mapp", help="Mapp med dygnsfiler (default data/json). Filerna ska ha nycklarna "
                                  "'sleep' och 'stress' i garminconnect-format.")
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
    elif args.cmd == "vaken":
        from pathlib import Path
        mapp = Path(args.mapp) if args.mapp else JSON_DIR
        filer = sorted(p for p in mapp.glob("????-??-??.json"))
        n = sum(uppdatera_vaken(con, p.stem, mapp) for p in filer)
        log(f"Stress under vaken tid räknad för {n} av {len(filer)} dygn i {mapp}.")
    elif args.cmd == "demo":
        import demo_data
        demo_data.create(con)
    return 0


if __name__ == "__main__":
    sys.exit(main())
