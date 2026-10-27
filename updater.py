"""Söker efter och installerar nya versioner från GitHub-releaser.

En release ska ha taggen v<version> (t.ex. v1.1.0) och zip-filen från skapa_paket.py som bifogad fil.
Bara programfilerna byts ut – mappen data/ (dygn, pass, inloggning) och .verktyg/ rörs aldrig."""
import io
import json
import os
import re
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from version import UPDATE_REPO, VERSION

ROOT = Path(__file__).resolve().parent
PROTECTED = ("data", ".verktyg", "dist")
RESTART_CODE = 3          # starta.py startar om dashboarden när den avslutas med denna kod
TIMEOUT = 15


def parse_version(v: str) -> tuple:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:3]) or (0,)


def latest_release() -> dict | None:
    """{'version', 'notes', 'url', 'published'} för senaste release, eller None om inget hittas."""
    if not UPDATE_REPO:
        return None
    req = urllib.request.Request(f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "HRV-data"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            rel = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:  # inga releaser publicerade än
            return None
        raise
    asset = next((a for a in rel.get("assets", []) if a.get("name", "").lower().endswith(".zip")), None)
    if not asset:
        return None
    return {"version": rel.get("tag_name", "").lstrip("vV"), "notes": rel.get("body") or "",
            "url": asset["browser_download_url"], "published": (rel.get("published_at") or "")[:10]}


def is_newer(release: dict | None) -> bool:
    return bool(release) and parse_version(release["version"]) > parse_version(VERSION)


def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "HRV-data"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def install(zip_bytes: bytes, root: Path = ROOT) -> dict:
    """Packar upp en paketzip över appmappen. Returnerar {'files': n, 'needs_full_restart': bool}.
    needs_full_restart = paketlistan har ändrats, så startfilen behöver köras igen för att installera."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        prefix = names[0].split("/")[0] + "/" if names and all("/" in n for n in names) else ""
        files = {}
        for n in names:
            rel = n[len(prefix):] if prefix and n.startswith(prefix) else n
            parts = Path(rel).parts
            if not parts or ".." in parts or Path(rel).is_absolute() or parts[0] in PROTECTED:
                continue
            files[rel] = z.read(n)
    if not {"dashboard.py", "version.py", "modell.py"} <= files.keys():
        raise ValueError("Filen ser inte ut som ett HRV-data-paket.")

    lock = root / "requirements-lock.txt"
    needs_full = "requirements-lock.txt" in files and (not lock.exists() or lock.read_bytes() != files["requirements-lock.txt"])
    written = 0
    for rel, data in files.items():
        target = root / rel
        if target.exists() and target.read_bytes() == data:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".ny")
        tmp.write_bytes(data)
        # Mac: startfilen måste förbli körbar efter en uppdatering (en ny fil får annars 644)
        if rel.endswith(".command") and os.name != "nt":
            tmp.chmod(0o755)
        tmp.replace(target)
        written += 1
    return {"files": written, "needs_full_restart": needs_full}
