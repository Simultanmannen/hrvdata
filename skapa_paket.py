"""Skapar dist/HRV-data-<version>.zip att ge till andra eller ladda upp som GitHub-release.
Innehåller bara appen – aldrig mappen data/ (inloggning och dygnsvärden), nedladdade verktyg,
provriggen i prov/, samla.py, rapport_bidrag.py eller referens.json. Releaserna är publika,
så inget med riktiga tal får följa med (regel 2 i CLAUDE.md).

    python skapa_paket.py

Ny version: höj VERSION i version.py först.
"""
import time
import zipfile
from pathlib import Path

from version import UPDATE_REPO, VERSION

ROOT = Path(__file__).resolve().parent
FILES = ["Starta HRV-data.bat", "Starta HRV-data.command", "LÄSMIG.txt", "starta.py", "dashboard.py", "modell.py", "planering.py", "passtyper.py", "labb.py", "berattelse.py", "bidrag.py", "db.py",
         "config.py", "sync.py", "oura.py", "jobs.py", "importera.py", "demo_data.py", "version.py", "updater.py",
         "requirements-lock.txt", ".streamlit/config.toml"]
# Spärr: de här får aldrig in i ett publikt paket, även om någon lägger till dem i FILES.
FORBJUDNA = {"referens.json", "samla.py", "rapport_bidrag.py", "CLAUDE.md"}
# Packas om de finns. grupp_prior.json (punkt 11) skrivs av samla.py --prior när minst tre bidrag med
# tillräcklig statistik finns; utan den räknar appen bara på personens egna data.
VALFRIA = ["grupp_prior.json"]


def fix_bat_line_endings() -> None:
    """cmd.exe läser bat-filer fel om de har LF-radslut (t.ex. efter redigering i vissa verktyg)."""
    for bat in ROOT.glob("*.bat"):
        data = bat.read_bytes()
        fixed = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        if fixed != data:
            bat.write_bytes(fixed)
            print(f"Rättade radslut i {bat.name}")


def main() -> None:
    fix_bat_line_endings()
    if FORBJUDNA & set(FILES):
        raise SystemExit(f"Får inte packas (publik release): {sorted(FORBJUDNA & set(FILES))}")
    missing = [f for f in FILES if not (ROOT / f).exists()]
    if missing:
        raise SystemExit(f"Saknas: {missing}")
    out = ROOT / "dist" / f"HRV-data-{VERSION}.zip"
    if out.exists():
        print(f"OBS: {out.name} finns redan och skrivs över. Glömde du höja VERSION i version.py?")
    out.parent.mkdir(exist_ok=True)
    filer = FILES + [f for f in VALFRIA if (ROOT / f).exists()]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in filer:
            data = (ROOT / f).read_bytes()
            if f.endswith((".bat", ".txt")):  # Windows vill ha CRLF
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            if f.endswith(".command"):  # bash på Mac kraschar på CRLF
                data = data.replace(b"\r\n", b"\n")
            # Unix-rättigheter i zip-filen: utan dem packar Mac upp startfilen som icke körbar.
            # Windows Utforskaren bryr sig inte om fälten.
            info = zipfile.ZipInfo(f"HRV-data/{f}", date_time=time.localtime()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o100755 if f.endswith(".command") else 0o100644) << 16
            z.writestr(info, data)
    print(f"Skapade {out} ({out.stat().st_size // 1024} kB), version {VERSION}")
    if not UPDATE_REPO:
        print("VARNING: UPDATE_REPO i version.py är tomt – den här versionen kan inte söka efter uppdateringar.")
    else:
        print(f"Ladda upp som release på https://github.com/{UPDATE_REPO}/releases/new med taggen v{VERSION}")


if __name__ == "__main__":
    main()
