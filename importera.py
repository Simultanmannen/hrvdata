"""Läser in en befintlig garmin.db (från Garmin-kitet eller ett eget hämtningsskript) i appens databas.

Båda har tabellerna days och activities med samma kolumnnamn som appen, så det är en ren kopiering
av de kolumner appen använder. Befintliga dygn och pass med samma nyckel skrivs över, exempeldata
tas bort. fetched_at följer med, så dygn som hämtades innan de var klara hämtas om vid nästa
hämtning i appen.

    python importera.py ..\\garmin.db
"""
import sqlite3
import sys
from pathlib import Path

import db
import sync


def importera(src_path, con) -> tuple[int, int]:
    """Returnerar (antal dygn, antal pass)."""
    src = sqlite3.connect(f"file:{Path(src_path).as_posix()}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"days", "activities"} <= tables:
            raise ValueError("Filen saknar tabellerna days och activities – är det en garmin.db?")
        dcols = {r[1] for r in src.execute("PRAGMA table_info(days)")}
        acols = {r[1] for r in src.execute("PRAGMA table_info(activities)")}
        if not {"date", "hrv_night", "hrv_7d", "stress_avg"} <= dcols:
            raise ValueError("Tabellen days har inte de kolumner som behövs (hrv_night, hrv_7d, stress_avg).")
        if not {"activity_id", "date", "type_key", "training_load"} <= acols:
            raise ValueError("Tabellen activities har inte de kolumner som behövs.")
        dc = [c for c in db.DAY_COLS if c in dcols]
        ac = [c for c in db.ACT_COLS if c in acols]
        days = src.execute(f"SELECT {','.join(dc)} FROM days").fetchall()
        acts = src.execute(f"SELECT {','.join(ac)} FROM activities").fetchall()
    finally:
        src.close()
    sync.remove_demo(con)
    with con:
        con.executemany(f"INSERT OR REPLACE INTO days ({','.join(dc)}) VALUES ({','.join('?' * len(dc))})", days)
        con.executemany(f"INSERT OR REPLACE INTO activities ({','.join(ac)}) VALUES ({','.join('?' * len(ac))})",
                        [(str(r[0]),) + tuple(r[1:]) for r in acts])
    return len(days), len(acts)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Användning: python importera.py <sökväg till garmin.db>")
    n_d, n_a = importera(sys.argv[1], db.connect())
    print(f"Inläst: {n_d} dygn och {n_a} pass till {db.DB_PATH}")
