"""Provrigg: fejkad Oura-server med känd sanning. Kör oura.py mot den och kontrollerar att
(1) OAuth-dansen går igenom, (2) days/activities fylls rätt, (3) modell.py återfinner sanningen.

    python prov_oura_mock.py
"""
import http.server
import json
import os
import sys
import threading
import urllib.parse
from datetime import date, datetime, timedelta

import numpy as np

PORT = 8799
N_DAYS = 800
rng = np.random.default_rng(11)
today = date.today()
DAYS = [today - timedelta(days=N_DAYS - i) for i in range(N_DAYS + 1)]
N = len(DAYS)

# --- sanning, samma struktur som demo_data.py ---
form = np.zeros(N)
for i in range(1, N):
    form[i] = 0.93 * form[i - 1] + rng.normal(0, 4.5 * np.sqrt(1 - 0.93 ** 2))
sick = np.zeros(N, bool)
for s in rng.choice(np.arange(30, N - 10), size=5, replace=False):
    sick[s:s + rng.integers(3, 7)] = True
WORK = []  # (i, activity, intensity, minutes, hour)
load = np.zeros(N)
INT = {"easy": 0.5, "moderate": 1.0, "hard": 2.0}
for i in range(N):
    if sick[i] or rng.random() > 0.55:
        continue
    act = rng.choice(["running", "cycling", "strength_training", "walking", "cross_country_skiing"])
    inten = rng.choice(["easy", "moderate", "hard"], p=[0.3, 0.45, 0.25])
    minutes = float(max(20, rng.normal(60, 20)))
    hour = int(rng.choice([7, 12, 17, 18]))
    WORK.append((i, act, inten, minutes, hour))
    load[i] += minutes * INT[inten]
ar = np.zeros(N)
for i in range(1, N):
    ar[i] = 0.5 * ar[i - 1] + rng.normal(0, 25)
stress_min = np.clip(np.round(90 + ar + 0.3 * load - 0.15 * np.r_[0, load[:-1]] + 30 * sick), 0, 400)  # minuter hög stress
hrv = np.full(N, np.nan)
for i in range(N - 1):
    hrv[i + 1] = 58 + form[i] - 0.045 * load[i] - 0.12 * (stress_min[i] - 90) - 5 * sick[i] + rng.normal(0, 7)
hrv = np.clip(np.round(hrv), 20, 140)
# Oura-stress finns bara sista 500 dygnen (lanserades hösten 2023)
STRESS_FROM = N - 500
TRUTH = dict(load=-0.045, stress=-0.12, form_phi=0.93)


def sleep_doc(i):
    d = DAYS[i]
    if not np.isfinite(hrv[i]):
        return None
    start = datetime.combine(d - timedelta(days=1), datetime.min.time()) + timedelta(hours=23)
    n5 = 90
    items = list(np.round(hrv[i] + np.linspace(-10, 8, n5) + rng.normal(0, 6, n5)).astype(float))
    return {"id": f"s{i}", "day": d.isoformat(), "type": "long_sleep", "average_hrv": float(hrv[i]),
            "lowest_heart_rate": float(round(48 + 4 * sick[i] - 0.1 * form[i] + rng.normal(0, 1.2))),
            "average_heart_rate": 55.0, "average_breath": float(round(14 + 1.3 * sick[i] + rng.normal(0, 0.3), 1)),
            "total_sleep_duration": int(rng.normal(7.4, 0.6) * 3600), "deep_sleep_duration": 5400,
            "rem_sleep_duration": 6000, "light_sleep_duration": 14000, "awake_time": 1200,
            "bedtime_start": start.isoformat() + "+01:00", "bedtime_end": (start + timedelta(hours=8)).isoformat() + "+01:00",
            "hrv": {"interval": 300.0, "items": items, "timestamp": start.isoformat() + "+01:00"},
            "readiness": {"temperature_deviation": float(round(0.5 * sick[i] + rng.normal(0, 0.2), 2))}}


def docs(kind, a, b):
    out = []
    for i, d in enumerate(DAYS):
        if not (a <= d.isoformat() <= b):
            continue
        if kind == "sleep":
            s = sleep_doc(i)
            if s:
                out.append(s)
                if rng.random() < 0.1:  # en tupplur ibland – ska INTE väljas
                    out.append({"id": f"n{i}", "day": d.isoformat(), "type": "late_nap", "average_hrv": 999.0,
                                "total_sleep_duration": 1800, "lowest_heart_rate": 60.0})
        elif kind == "daily_sleep":
            out.append({"id": f"ds{i}", "day": d.isoformat(), "score": int(np.clip(rng.normal(75, 9), 30, 100))})
        elif kind == "daily_readiness":
            out.append({"id": f"dr{i}", "day": d.isoformat(), "score": 70,
                        "temperature_deviation": float(round(0.5 * sick[i] + rng.normal(0, 0.2), 2))})
        elif kind == "daily_stress":
            if i >= STRESS_FROM:
                out.append({"id": f"st{i}", "day": d.isoformat(), "stress_high": int(stress_min[i] * 60),
                            "recovery_high": 3600, "day_summary": "normal"})
        elif kind == "daily_activity":
            out.append({"id": f"da{i}", "day": d.isoformat(), "steps": int(max(1500, rng.normal(9000, 2500))),
                        "high_activity_time": 600, "medium_activity_time": 3000, "low_activity_time": 12000})
        elif kind == "workout":
            for (wi, act, inten, minutes, hour) in WORK:
                if wi == i:
                    st = datetime.combine(d, datetime.min.time()) + timedelta(hours=hour)
                    out.append({"id": f"w{wi}", "day": d.isoformat(), "activity": act, "intensity": inten,
                                "start_datetime": st.isoformat() + "+01:00",
                                "end_datetime": (st + timedelta(minutes=minutes)).isoformat() + "+01:00",
                                "calories": 400.0, "distance": 8000.0, "label": None, "source": "manual"})
    return out


TOKENS = {"issued": 0, "refreshed": 0}


class H(http.server.BaseHTTPRequestHandler):
    def _json(self, obj, code=200, headers=None):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path == "/oauth/authorize":   # "användaren godkänner" → tillbaka till appen
            loc = q["redirect_uri"] + "?" + urllib.parse.urlencode({"code": "KOD123", "state": q["state"]})
            self.send_response(302); self.send_header("Location", loc); self.end_headers(); return
        if not self.headers.get("Authorization", "").startswith("Bearer tok"):
            return self._json({"detail": "unauthorized"}, 401)
        if self.headers.get("Authorization") == "Bearer tok-gammal":
            return self._json({"detail": "expired"}, 401)
        kind = u.path.rsplit("/", 1)[-1]
        if kind == "heartrate":
            a = q["start_datetime"][:19]; b = q["end_datetime"][:19]
            t0, t1 = datetime.fromisoformat(a), datetime.fromisoformat(b)
            rows, t = [], t0
            while t <= t1:
                rows.append({"bpm": int(rng.normal(140, 12)), "source": "workout", "timestamp": t.isoformat()})
                t += timedelta(minutes=5)
            return self._json({"data": rows, "next_token": None})
        data = docs(kind, q["start_date"], q["end_date"])
        # sidbläddring: 40 poster per sida
        page = int(q.get("next_token", "0"))
        chunk = data[page * 40:(page + 1) * 40]
        nxt = str(page + 1) if len(data) > (page + 1) * 40 else None
        # simulera 429 en gång
        if kind == "sleep" and page == 0 and not getattr(H, "rl_done", False):
            H.rl_done = True
            return self._json({"detail": "slow down"}, 429, {"Retry-After": "1"})
        self._json({"data": chunk, "next_token": nxt})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = urllib.parse.parse_qs(self.rfile.read(n).decode())
        g = body.get("grant_type", [""])[0]
        if g == "authorization_code" and body.get("code") == ["KOD123"] and body.get("client_secret") == ["hemlig"]:
            TOKENS["issued"] += 1
            return self._json({"access_token": "tok-1", "refresh_token": "ref-1", "expires_in": 2592000, "token_type": "bearer"})
        if g == "refresh_token" and body.get("refresh_token") == [f"ref-{TOKENS['refreshed'] + 1}"]:
            TOKENS["refreshed"] += 1
            k = TOKENS["refreshed"] + 1
            return self._json({"access_token": f"tok-{k}", "refresh_token": f"ref-{k}", "expires_in": 2592000})
        self._json({"error": "invalid_grant", "error_description": "fel kod eller secret"}, 400)


def serve():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    import shutil, tempfile, time
    tmp = tempfile.mkdtemp(prefix="hrvdata_oura_")
    os.environ["HRVDATA_DIR"] = tmp
    os.environ["OURA_API_BASE"] = f"http://127.0.0.1:{PORT}"
    os.environ["OURA_AUTH_URL"] = f"http://127.0.0.1:{PORT}/oauth/authorize"
    serve()
    import requests
    import db, oura, modell, bidrag
    oura.PAUSE_S = 0.0

    # 1. OAuth: "webbläsaren" = en tråd som följer authorize-redirecten till localhost-callbacken
    def browser():
        time.sleep(0.5)   # browser.url sätts av spy() när appen bygger sin authorize-URL
        requests.get(browser.url, allow_redirects=True, timeout=10)
    real_auth_url = oura.auth_url
    def spy(cid, state):
        browser.url = real_auth_url(cid, state); return browser.url
    oura.auth_url = spy
    threading.Thread(target=browser, daemon=True).start()
    try:
        oura.authorize("klient", "fel-secret", timeout_s=10, open_browser=False)
        raise SystemExit("FEL: fel secret borde ha nekats")
    except oura.OuraAuthError as e:
        print("1a. fel secret nekas:", e)
    threading.Thread(target=browser, daemon=True).start()
    tok = oura.authorize("klient", "hemlig", timeout_s=10, open_browser=False)
    assert oura.is_logged_in() and tok["access_token"] == "tok-1", tok
    print("1b. OAuth ok, token sparad i", oura.TOKEN_FILE)

    # 2. token-förnyelse: låtsas att access-token gått ut
    t = oura._load(); t["expires_at"] = 0; oura._save(t)
    assert oura.access_token() == "tok-2" and oura._load()["refresh_token"] == "ref-2"
    print("2. refresh ok (nytt refresh-token sparat)")

    # 3. hämtning
    con = db.connect()
    t0 = time.time()
    n_new = oura.sync_activities(con, (today - timedelta(days=N_DAYS)).isoformat(), full=True)
    n_days = oura.sync_days(con, N_DAYS + 1)
    print(f"3. {n_new} pass, {n_days} dygn på {time.time() - t0:.1f} s (inkl. en simulerad 429)")
    assert n_new == len(WORK), (n_new, len(WORK))
    r = con.execute("SELECT count(*), count(hrv_night), count(hrv_7d), count(stress_avg), count(rhr) FROM days").fetchone()
    print("   days:", r)
    assert r[3] == 500, "stress ska finnas för 500 dygn"
    bad = con.execute("SELECT count(*) FROM days WHERE hrv_night=999").fetchone()[0]
    assert bad == 0, "tupplur valdes som huvudsömn"
    a = con.execute("SELECT avg_hr, max_hr, training_load, duration_s, type_key, start_local FROM activities LIMIT 1").fetchone()
    print("   ett pass:", a)
    assert a[0] and a[1] and a[2]
    # hrv_7d = medel av 7 nätter
    chk = con.execute("SELECT date, hrv_7d FROM days WHERE hrv_7d IS NOT NULL ORDER BY date DESC LIMIT 1").fetchone()
    d = date.fromisoformat(chk[0]); xs = [hrv[DAYS.index(d - timedelta(days=k))] for k in range(7)]
    assert abs(np.nanmean(xs) - chk[1]) < 1e-6
    print("   hrv_7d stämmer")
    # 4. inkrementell körning: inga nya pass, klara dygn hoppas över
    n2 = oura.sync_activities(con, (today - timedelta(days=30)).isoformat(), full=False)
    n2d = oura.sync_days(con, 30)
    print(f"4. inkrementell: {n2} nya pass, {n2d} dygn omhämtade (de 2 senaste räknas som ofullständiga)")
    assert n2 == 0 and n2d <= 3

    # 5. modellen
    R = modell.kor(con)
    assert R["ok"], R
    kt, ks = R['koef_total']['load'], R['koef']['stress']
    g = lambda c: c['b'] if isinstance(c, dict) else c
    print(f"5. modell: n={R['n']} hl={R['hl']} last total {g(kt):+.4f} (sant {TRUTH['load']}) "
          f"stress {g(ks):+.4f} (sant {TRUTH['stress']})  R2 {R['r2']}")
    b = bidrag.bygg(R, "prov", {}, kalla="oura")
    assert b["beskrivning"]["kalla"] == "oura"
    print("   bidragsfil ok, typfördelning:", b["beskrivning"]["typfordelning_min"])
    # rå JSON med 5-min-HRV finns
    j = json.loads((oura.JSON_DIR / DAYS[-3].isoformat()).with_suffix(".json").read_text())
    assert len(j["sleep"][0]["hrv"]["items"]) == 90
    print("6. rå JSON per dygn med 5-min-HRV sparad")
    con.close()
    shutil.rmtree(tmp, ignore_errors=True)
    print("ALLT OK")
