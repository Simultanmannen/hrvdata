"""Startar HRV-data: väljer en ledig port, startar dashboarden och öppnar webbläsaren.
Avslutas dashboarden med kod 3 (efter en uppdatering) startas den om på samma port, och
webbläsarfliken ansluter av sig själv."""
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESTART_CODE = 3


def free_port(start: int = 8601) -> int:
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("Hittade ingen ledig port")


def run_server(port: int) -> subprocess.Popen | None:
    url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "dashboard.py"),
         "--server.port", str(port), "--server.address", "127.0.0.1"],
        cwd=ROOT)
    for _ in range(240):
        if proc.poll() is not None:
            print("\nDashboarden kunde inte starta. Se felmeddelandet ovan.", flush=True)
            return None
        try:
            urllib.request.urlopen(f"{url}/_stcore/health", timeout=1)
            return proc
        except OSError:
            time.sleep(0.5)
    return proc


def main() -> int:
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    first = True
    while True:
        proc = run_server(port)
        if proc is None:
            return 1
        if first:
            if not os.environ.get("HRVDATA_NO_BROWSER"):
                webbrowser.open(url)
            print("\n" + "=" * 60)
            print(f"  HRV-data körs nu i webbläsaren: {url}")
            print("  Låt det här fönstret vara öppet medan du använder appen.")
            print("  Stäng fönstret när du är klar.")
            print("=" * 60 + "\n", flush=True)
            first = False
        try:
            code = proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            return 0
        if code != RESTART_CODE:
            return code
        print("  Uppdatering installerad – startar om …", flush=True)
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
