"""Sökvägar och gemensamma inställningar. All data ligger lokalt under ./data."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# Datamappen kan flyttas med miljövariabeln HRVDATA_DIR
DATA_DIR = Path(os.environ.get("HRVDATA_DIR") or ROOT / "data")
JSON_DIR = DATA_DIR / "json"            # rå JSON från Garmin Connect, en fil per dygn
TOKEN_DIR = DATA_DIR / "garmin_tokens"  # Garmin-token (inte lösenord). Oura-token: data/oura_tokens.json
DB_PATH = DATA_DIR / "hrv.db"
BIDRAG_DIR = DATA_DIR / "bidrag"        # sparade bidragsfiler

# Vart bidragsfilen ska skickas. Visas i fliken Bidra. Tomt = ingen adress visas.
BIDRAG_MOTTAGARE = ""


def ensure_dirs() -> None:
    for d in (DATA_DIR, JSON_DIR, BIDRAG_DIR):
        d.mkdir(parents=True, exist_ok=True)
