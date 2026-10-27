"""SQLite-databas med dygnsvärden och pass. Kolumnnamnen är desamma som i Garmin-kitets
garmin.db, så att en sådan databas kan läsas in direkt."""
import sqlite3
import uuid

from config import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS days (
    date             TEXT PRIMARY KEY,
    hrv_night        REAL,     -- Garmins lastNightAvg (ms)
    hrv_7d           REAL,     -- Garmins weeklyAvg (ms)
    hrv_status       TEXT,
    rhr              REAL,
    respiration_avg  REAL,
    skin_temp_dev_c  REAL,
    stress_avg       REAL,     -- dagssnitt; saknas = NULL
    sleep_seconds    REAL,
    sleep_score      REAL,
    deep_seconds     REAL,
    rem_seconds      REAL,
    awake_seconds    REAL,
    steps            REAL,
    fetched_at       TEXT      -- när dygnet hämtades ('demo' för exempeldata)
);

CREATE TABLE IF NOT EXISTS activities (
    activity_id    TEXT PRIMARY KEY,
    date           TEXT,
    type_key       TEXT,
    name           TEXT,
    start_local    TEXT,
    duration_s     REAL,
    distance_m     REAL,
    avg_hr         REAL,
    max_hr         REAL,
    aerobic_te     REAL,
    anaerobic_te   REAL,
    training_load  REAL,
    calories       REAL
);
CREATE INDEX IF NOT EXISTS ix_act_date ON activities(date);

CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
"""
DAY_COLS = ["date", "hrv_night", "hrv_7d", "hrv_status", "rhr", "respiration_avg", "skin_temp_dev_c",
            "stress_avg", "sleep_seconds", "sleep_score", "deep_seconds", "rem_seconds",
            "awake_seconds", "steps", "fetched_at"]
ACT_COLS = ["activity_id", "date", "type_key", "name", "start_local", "duration_s", "distance_m",
            "avg_hr", "max_hr", "aerobic_te", "anaerobic_te", "training_load", "calories"]


def connect(path=DB_PATH) -> sqlite3.Connection:
    ensure_dirs()
    # timeout + WAL: dashboarden läser medan hämtningen skriver i bakgrunden
    con = sqlite3.connect(path, timeout=30, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def _upsert(con, table, cols, row: dict):
    con.execute(f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                [row.get(c) for c in cols])


def save_day(con, row: dict) -> None:
    with con:
        _upsert(con, "days", DAY_COLS, row)


def save_activities(con, rows: list[dict]) -> None:
    with con:
        for r in rows:
            _upsert(con, "activities", ACT_COLS, r)


def known_activity_ids(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT activity_id FROM activities")}


def day_status(con) -> dict[str, tuple]:
    """{datum: (fetched_at, har_hrv)} för alla sparade dygn."""
    return {d: (f, h is not None) for d, f, h in con.execute("SELECT date, fetched_at, hrv_night FROM days")}


def meta_get(con, key: str, default=None):
    r = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def meta_set(con, key: str, value) -> None:
    with con:
        con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, None if value is None else str(value)))


def participant_id(con) -> str:
    """Slumpat id som skiljer bidrag åt utan att säga vem man är. Skapas första gången."""
    pid = meta_get(con, "deltagare")
    if not pid:
        pid = uuid.uuid4().hex[:12]
        meta_set(con, "deltagare", pid)
    return pid
