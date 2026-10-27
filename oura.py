"""Hämtar dygnsvärden och pass från Oura (API v2) till samma lokala databas som Garmin-hämtningen,
så att modellen och dashboarden kör oförändrade.

Inloggning: Oura tog bort personliga åtkomsttoken i december 2025, så det som finns är OAuth2.
Användaren registrerar en egen "API Application" på https://cloud.ouraring.com/oauth/applications
(gratis, i det egna Oura-kontot) med omdirigerings-URL  http://localhost:8765/callback  och klistrar
in Client ID och Client Secret i appen. Appen öppnar webbläsaren för ett godkännande en gång och
sparar sedan token i data/oura_tokens.json. Inget lösenord passerar appen.

Mappning till days-tabellen (Garmins fältnamn):
  hrv_night        sleep.average_hrv (RMSSD, ms) för nattens huvudsömn
  hrv_7d           räknas här: medel av hrv_night de senaste 7 nätterna t.o.m. dagen (minst 4)
  rhr              sleep.lowest_heart_rate
  respiration_avg  sleep.average_breath
  skin_temp_dev_c  daily_readiness.temperature_deviation
  stress_avg       daily_stress.stress_high i MINUTER (Oura saknar Garmins 0–100-skala; finns
                   från hösten 2023 och bara dagar ringen burits på dagen)
  sleep_seconds, deep/rem/awake, sleep_score, steps  från sleep, daily_sleep, daily_activity
Pass (activities): workout-endpointen. Oura har ingen EPOC-baserad träningslast, så
  training_load = minuter × vikt (easy 0,5 / moderate 1,0 / hard 2,0), dvs. ett hårt entimmespass = 120.
  avg_hr/max_hr räknas ur heartrate-endpointen under passet när den finns.

    python oura.py login     (frågar efter client id/secret och öppnar webbläsaren)
    python oura.py sync --days 1095
"""
import argparse
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import date, datetime, timedelta

import requests

import db
from config import DATA_DIR, JSON_DIR, ensure_dirs

API = os.environ.get("OURA_API_BASE", "https://api.ouraring.com")
AUTH_URL = os.environ.get("OURA_AUTH_URL", "https://cloud.ouraring.com/oauth/authorize")
TOKEN_URL = API + "/oauth/token"
APP_REG_URL = "https://cloud.ouraring.com/oauth/applications"
REDIRECT_PORT = int(os.environ.get("OURA_REDIRECT_PORT", "8765"))
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
SCOPES = "personal daily heartrate workout"
TOKEN_FILE = DATA_DIR / "oura_tokens.json"
WINDOW_DAYS = 30          # dygn per API-anrop
PAUSE_S = 0.15
UTAN_HRV_STOPP = 120      # dygn i rad utan HRV bakåt = ringen fanns inte längre bak
INTENSITET = {"easy": 0.5, "moderate": 1.0, "hard": 2.0}
log = print               # dashboarden byter ut den här


class OuraAuthError(Exception):
    pass


# ---------------------------------------------------------------- token
def _load() -> dict:
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(tok: dict) -> None:
    ensure_dirs()
    TOKEN_FILE.write_text(json.dumps(tok, indent=1), encoding="utf-8")
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass


def is_logged_in() -> bool:
    return bool(_load().get("refresh_token"))


def logout() -> None:
    TOKEN_FILE.unlink(missing_ok=True)


def friendly_error(e: Exception) -> str:
    name, text = type(e).__name__, str(e).lower()
    if isinstance(e, OuraAuthError):
        return str(e)
    if "429" in text:
        return "Oura säger att det har varit för många anrop. Vänta några minuter och försök igen."
    if "401" in text or "403" in text:
        return "Oura godkänner inte inloggningen längre. Anslut Oura igen under Data & konto."
    if "Connection" in name or "timeout" in text or "resolve" in text:
        return "Kunde inte nå Oura. Kontrollera internetanslutningen och försök igen."
    return f"Något gick fel mot Oura ({name}). Försök igen om en stund."


def auth_url(client_id: str, state: str) -> str:
    q = dict(response_type="code", client_id=client_id, redirect_uri=REDIRECT_URI, scope=SCOPES, state=state)
    return AUTH_URL + "?" + urllib.parse.urlencode(q)


def _token_request(data: dict) -> dict:
    r = requests.post(TOKEN_URL, data=data, timeout=30)
    if r.status_code != 200:
        try:
            msg = r.json().get("error_description") or r.json().get("error") or r.text
        except ValueError:
            msg = r.text
        raise OuraAuthError(f"Oura svarade {r.status_code} vid inloggningen: {msg[:200]}")
    tok = r.json()
    tok["expires_at"] = time.time() + float(tok.get("expires_in", 86400)) - 60
    return tok


def exchange_code(client_id: str, client_secret: str, code: str) -> dict:
    tok = _token_request(dict(grant_type="authorization_code", code=code, redirect_uri=REDIRECT_URI,
                              client_id=client_id, client_secret=client_secret))
    tok.update(client_id=client_id, client_secret=client_secret)
    _save(tok)
    return tok


def refresh() -> dict:
    tok = _load()
    if not tok.get("refresh_token"):
        raise OuraAuthError("Ingen sparad Oura-inloggning. Anslut Oura under Data & konto.")
    new = _token_request(dict(grant_type="refresh_token", refresh_token=tok["refresh_token"],
                              client_id=tok["client_id"], client_secret=tok["client_secret"]))
    tok.update(new)          # refresh-token är engångs: det nya måste sparas direkt
    _save(tok)
    return tok


def access_token() -> str:
    tok = _load()
    if not tok.get("access_token"):
        raise OuraAuthError("Ingen sparad Oura-inloggning. Anslut Oura under Data & konto.")
    if time.time() > tok.get("expires_at", 0):
        tok = refresh()
    return tok["access_token"]


class _Callback(http.server.BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path != "/callback":
            self.send_response(404); self.end_headers(); return
        _Callback.result = {k: v[0] for k, v in q.items()}
        body = ("<html><body style='font-family:sans-serif;padding:2em'><h2>Klart!</h2>"
                "<p>Oura är anslutet till HRV-data. Du kan stänga den här fliken och gå tillbaka till appen.</p>"
                "</body></html>").encode("utf-8")
        if "error" in q:
            body = f"<html><body><h2>Oura nekade</h2><p>{q['error'][0]}</p></body></html>".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # tyst
        pass


def authorize(client_id: str, client_secret: str, timeout_s: int = 240, open_browser: bool = True) -> dict:
    """Hela OAuth-dansen: lyssnar på localhost, öppnar webbläsaren, väntar på koden, byter till token.
    Blockerar tills användaren godkänt eller timeout."""
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id or not client_secret:
        raise OuraAuthError("Fyll i både Client ID och Client Secret.")
    state = secrets.token_urlsafe(16)
    _Callback.result = {}
    try:
        srv = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), _Callback)
    except OSError as e:
        raise OuraAuthError(f"Port {REDIRECT_PORT} är upptagen på datorn ({e}). Stäng andra program och försök igen.")
    srv.timeout = 1
    url = auth_url(client_id, state)
    if open_browser:
        webbrowser.open(url)
    log(f"Öppna i webbläsaren om den inte öppnades av sig själv:\n{url}")
    t0 = time.time()
    try:
        while time.time() - t0 < timeout_s and not _Callback.result:
            srv.handle_request()
    finally:
        srv.server_close()
    res = _Callback.result
    if not res:
        raise OuraAuthError("Inget svar från Oura inom tiden. Försök igen och godkänn i webbläsaren.")
    if "error" in res:
        raise OuraAuthError(f"Oura nekade: {res.get('error_description') or res['error']}")
    if res.get("state") != state:
        raise OuraAuthError("Svaret från Oura hörde inte till den här förfrågan. Försök igen.")
    return exchange_code(client_id, client_secret, res["code"])


# ---------------------------------------------------------------- API
def _get(path: str, params: dict) -> list[dict]:
    """GET med sidbläddring (next_token) och väntan vid 429. Returnerar alla 'data'-poster."""
    out, params = [], dict(params)
    while True:
        for attempt in range(5):
            r = requests.get(f"{API}/v2/usercollection/{path}", params=params,
                             headers={"Authorization": f"Bearer {access_token()}"}, timeout=60)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After") or 30 * (attempt + 1))
                log(f"Oura vill att vi saktar ner – väntar {wait} s …")
                time.sleep(min(wait, 300))
                continue
            if r.status_code == 401 and attempt == 0:
                refresh()
                continue
            break
        if r.status_code != 200:
            raise requests.HTTPError(f"{r.status_code} från Oura ({path}): {r.text[:200]}")
        j = r.json()
        out.extend(j.get("data") or [])
        nt = j.get("next_token")
        if not nt:
            return out
        params["next_token"] = nt
        time.sleep(PAUSE_S)


def _windows(start: date, end: date):
    a = start
    while a <= end:
        b = min(a + timedelta(days=WINDOW_DAYS - 1), end)
        yield a.isoformat(), b.isoformat()
        a = b + timedelta(days=1)


# ---------------------------------------------------------------- pass
def _hr_stats(start_dt: str, end_dt: str) -> tuple[float | None, float | None]:
    try:
        rows = _get("heartrate", {"start_datetime": start_dt, "end_datetime": end_dt})
    except requests.HTTPError:
        return None, None
    bpm = [r["bpm"] for r in rows if r.get("bpm")]
    if len(bpm) < 3:
        return None, None
    return float(sum(bpm) / len(bpm)), float(max(bpm))


def workout_row(w: dict, avg_hr=None, max_hr=None) -> dict:
    start = (w.get("start_datetime") or "")[:19]
    end = (w.get("end_datetime") or "")[:19]
    dur = None
    if start and end:
        dur = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
    vikt = INTENSITET.get((w.get("intensity") or "moderate").lower(), 1.0)
    return {
        "activity_id": "oura-" + str(w.get("id") or f"{w.get('day')}-{start}"),
        "date": (w.get("day") or start[:10]) or None,
        "type_key": (w.get("activity") or "other").lower(),
        "name": w.get("label") or w.get("activity"),
        "start_local": start.replace("T", " ") or None,
        "duration_s": dur,
        "distance_m": w.get("distance"),
        "avg_hr": avg_hr,
        "max_hr": max_hr,
        "aerobic_te": None,
        "anaerobic_te": None,
        "training_load": (dur / 60.0) * vikt if dur else None,
        "calories": w.get("calories"),
    }


def sync_activities(con, since: str | None, full: bool, progress=None) -> int:
    known = db.known_activity_ids(con)
    start = date.fromisoformat(since) if since else date.today() - timedelta(days=3650)
    new = 0
    for a, b in _windows(start, date.today()):
        ws = _get("workout", {"start_date": a, "end_date": b})
        rows = []
        for w in ws:
            aid = "oura-" + str(w.get("id") or f"{w.get('day')}-{(w.get('start_datetime') or '')[:19]}")
            if aid in known and not full:
                continue
            hr = (None, None)
            if w.get("start_datetime") and w.get("end_datetime"):
                hr = _hr_stats(w["start_datetime"], w["end_datetime"])
                time.sleep(PAUSE_S)
            rows.append(workout_row(w, *hr))
            if aid not in known:
                new += 1
        db.save_activities(con, rows)
        if progress:
            progress("list", new, None)
        time.sleep(PAUSE_S)
    log(f"{new} nya pass.")
    return new


# ---------------------------------------------------------------- dygn
def _main_sleep(docs: list[dict]) -> dict | None:
    if not docs:
        return None
    long = [s for s in docs if s.get("type") == "long_sleep"]
    pool = long or docs
    return max(pool, key=lambda s: s.get("total_sleep_duration") or 0)


def day_row(d: str, raw: dict, fetched_at: str) -> dict:
    s = _main_sleep(raw.get("sleep") or []) or {}
    ds = (raw.get("daily_sleep") or [{}])[0]
    rd = (raw.get("daily_readiness") or [{}])[0]
    st = (raw.get("daily_stress") or [{}])[0]
    da = (raw.get("daily_activity") or [{}])[0]
    stress_high = st.get("stress_high")
    return {
        "date": d,
        "hrv_night": s.get("average_hrv"),
        "hrv_7d": None,                     # räknas i recompute_7d
        "hrv_status": None,
        "rhr": s.get("lowest_heart_rate"),
        "respiration_avg": s.get("average_breath"),
        "skin_temp_dev_c": rd.get("temperature_deviation"),
        "stress_avg": (stress_high / 60.0) if stress_high is not None else None,
        "sleep_seconds": s.get("total_sleep_duration"),
        "sleep_score": ds.get("score"),
        "deep_seconds": s.get("deep_sleep_duration"),
        "rem_seconds": s.get("rem_sleep_duration"),
        "awake_seconds": s.get("awake_time"),
        "steps": da.get("steps"),
        "fetched_at": fetched_at,
    }


def complete(d: str, fetched_at: str | None) -> bool:
    if not fetched_at or fetched_at == "demo":
        return False
    return fetched_at[:10] >= (date.fromisoformat(d) + timedelta(days=2)).isoformat()


def recompute_7d(con) -> None:
    """hrv_7d = medel av hrv_night de senaste 7 nätterna t.o.m. dagen (minst 4 värden). Motsvarar
    Garmins weeklyAvg och är känt innan utfallsnatten."""
    rows = con.execute("SELECT date, hrv_night FROM days WHERE fetched_at != 'demo' ORDER BY date").fetchall()
    vals = {date.fromisoformat(d).toordinal(): h for d, h in rows}
    upd = []
    for d, _ in rows:
        o = date.fromisoformat(d).toordinal()
        xs = [vals[k] for k in range(o - 6, o + 1) if vals.get(k) is not None]
        upd.append((sum(xs) / len(xs) if len(xs) >= 4 else None, d))
    with con:
        con.executemany("UPDATE days SET hrv_7d=? WHERE date=?", upd)


def sync_days(con, days_back: int, progress=None) -> int:
    """Hämtar dygnsvärden 30 dygn åt gången från idag och bakåt. Klara dygn hoppas över. Stannar när
    UTAN_HRV_STOPP dygn i rad saknar HRV bakåt i tiden."""
    status = db.day_status(con)
    today = date.today()
    first = today - timedelta(days=days_back - 1)
    fetched, utan_hrv, done = 0, 0, 0
    # fönster från idag och bakåt
    ends = []
    b = today
    while b >= first:
        a = max(first, b - timedelta(days=WINDOW_DAYS - 1))
        ends.append((a, b))
        b = a - timedelta(days=1)
    for a, b in ends:
        days = [(b - timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]
        need = [d for d in days if not (d in status and complete(d, status[d][0]))]
        raw_by_day: dict[str, dict] = {}
        if need:
            # Oura levererar alla dygn i fönstret på en gång; sömnen behöver en dag extra bakåt
            # eftersom en natt kan ligga på 'day' = morgondatumet men börja dagen före.
            p = {"start_date": a.isoformat(), "end_date": b.isoformat()}
            for key in ("sleep", "daily_sleep", "daily_readiness", "daily_stress", "daily_activity"):
                try:
                    docs = _get(key, p)
                except requests.HTTPError as e:
                    log(f"{key}: {e}")
                    docs = []
                for doc in docs:
                    dd = doc.get("day")
                    if dd:
                        raw_by_day.setdefault(dd, {}).setdefault(key, []).append(doc)
                time.sleep(PAUSE_S)
        now = datetime.now().isoformat(timespec="seconds")
        for d in days:
            done += 1
            if progress:
                progress("days", done, days_back)
            if d in need:
                raw = raw_by_day.get(d, {})
                (JSON_DIR / f"{d}.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
                row = day_row(d, raw, now)
                db.save_day(con, row)
                har_hrv = row["hrv_night"] is not None
                fetched += 1
            else:
                har_hrv = status[d][1]
            utan_hrv = 0 if har_hrv else utan_hrv + 1
            if utan_hrv >= UTAN_HRV_STOPP and done >= UTAN_HRV_STOPP:
                log(f"Ingen HRV på {UTAN_HRV_STOPP} dygn i rad före {d} – ringen mätte inte längre bak. Stannar.")
                recompute_7d(con)
                log(f"{fetched} dygn hämtade.")
                return fetched
    recompute_7d(con)
    log(f"{fetched} dygn hämtade.")
    return fetched


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description="Hämta Oura-data till lokal databas")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="Anslut Oura (OAuth2) och spara token")
    p = sub.add_parser("sync", help="Hämta pass och dygnsvärden")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--full", action="store_true")
    args = ap.parse_args(argv)
    ensure_dirs()
    con = db.connect()
    if args.cmd == "login":
        print(f"Registrera först en API Application på {APP_REG_URL} med Redirect URI {REDIRECT_URI}")
        cid = input("Client ID: ")
        sec = input("Client Secret: ")
        authorize(cid, sec)
        print(f"Oura anslutet. Token sparad i {TOKEN_FILE}.")
    elif args.cmd == "sync":
        import sync as garmin_sync
        garmin_sync.remove_demo(con)
        since = (date.today() - timedelta(days=args.days)).isoformat()
        sync_activities(con, since, args.full)
        sync_days(con, args.days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
