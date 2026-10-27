"""Hämtning från Garmin i en bakgrundstråd, så att dashboarden går att använda under tiden
och en lång historikhämtning inte avbryts om man klickar runt.

Modulen laddas en gång per server, så JOB delas mellan omladdningar av sidan."""
import threading
import time
from datetime import date, timedelta

import db
import oura
import sync

PHASES = {"list": "Listar pass", "days": "Hämtar dygnsvärden (HRV, sömn, stress)",
          "stress": "Kompletterar stressen under dagen för de senaste månaderna"}


def source() -> str:
    """Vilken källa som är ansluten: 'oura', 'garmin' eller ''."""
    if oura.is_logged_in():
        return "oura"
    if sync.is_logged_in():
        return "garmin"
    return ""


def source_name() -> str:
    return {"oura": "Oura", "garmin": "Garmin Connect"}.get(source(), "klockan")


class Job:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.reset()

    def reset(self):
        self.phase, self.done, self.total = "", 0, 0
        self.lines: list[str] = []
        self.error: Exception | None = None
        self.new = 0
        self.started_at = self.finished_at = self.phase_started = None
        self.acknowledged = True

    def add_line(self, msg) -> None:
        with self.lock:
            self.lines = (self.lines + [str(msg).strip()])[-8:]

    def progress(self, phase, done, total) -> None:
        with self.lock:
            if phase != self.phase:
                self.phase_started = time.time()
            self.phase, self.done, self.total = phase, done, total or 0

    @property
    def fraction(self) -> float | None:
        return min(1.0, self.done / self.total) if self.total else None

    def eta_text(self) -> str:
        if self.phase != "days" or not self.total or self.done < 10 or not self.phase_started:
            return ""
        per = (time.time() - self.phase_started) / max(self.done, 1)
        left = int(per * (self.total - self.done))
        return f"högst ca {left // 60} min kvar" if left >= 90 else "snart klart"


JOB = Job()


def start(days_back: int = 30, full: bool = False) -> bool:
    """Startar en hämtning om ingen redan pågår. days_back styr både pass och dygnsvärden."""
    with JOB.lock:
        if JOB.running:
            return False
        JOB.reset()
        JOB.running, JOB.acknowledged = True, False
        JOB.started_at = JOB.phase_started = time.time()
    threading.Thread(target=_run, args=(days_back, full), daemon=True).start()
    return True


def _run(days_back, full):
    sync.log = oura.log = JOB.add_line
    con = None
    try:
        src = source()
        con = db.connect()
        since = (date.today() - timedelta(days=days_back)).isoformat()
        if src == "oura":
            oura.access_token()  # förnyar token vid behov, eller kastar OuraAuthError
            sync.remove_demo(con)
            JOB.new = oura.sync_activities(con, since, full, progress=JOB.progress)
            oura.sync_days(con, days_back, progress=JOB.progress)
        else:
            api = sync.garmin_client(interactive=False)
            sync.remove_demo(con)
            JOB.new = sync.sync_activities(api, con, since, full, progress=JOB.progress)
            sync.sync_days(api, con, days_back, progress=JOB.progress)
        db.meta_set(con, "kalla", src)
        db.meta_set(con, "senast_hamtat", date.today().isoformat())
    except Exception as e:
        JOB.error = e
        if "Authentication" in type(e).__name__:
            sync.logout()  # sparad inloggning fungerar inte längre
    finally:
        sync.log = oura.log = print
        if con is not None:
            con.close()
        with JOB.lock:
            JOB.running = False
            JOB.finished_at = time.time()
