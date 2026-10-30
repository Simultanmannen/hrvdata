"""Kör dashboarden headless (Streamlit AppTest) i tre lägen: tom databas (inloggningssidan med
Garmin/Oura-flikar), Oura-ansluten med data (alla flikar), och Garmin-exempeldata (regression)."""
import os
import shutil
import sys
import tempfile
import threading
import time
from datetime import date, timedelta

tmp = tempfile.mkdtemp(prefix="hrvdata_dash_")
os.environ["HRVDATA_DIR"] = tmp
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prov_oura_mock as M  # startar inte servern förrän serve()
os.environ["OURA_API_BASE"] = f"http://127.0.0.1:{M.PORT}"
os.environ["OURA_AUTH_URL"] = f"http://127.0.0.1:{M.PORT}/oauth/authorize"
M.serve()
from streamlit.testing.v1 import AppTest

import db, oura, jobs
oura.PAUSE_S = 0.0


APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dashboard.py")


def run(label):
    # AppTest löser relativa sökvägar mot det anropande skriptet, alltså prov/, inte appmappen
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    errs = [e.value for e in at.error] + [str(x) for x in at.exception]
    print(f"{label}: {len(at.tabs)} flikar, {len(at.error)} st.error, {len(at.exception)} exception")
    for e in errs:
        print("   !!", str(e)[:300])
    return at, errs


# 1. tom databas → välkomstsida med Garmin/Oura-flikar
at, errs = run("tom databas")
assert not errs
labels = [t.label for t in at.tabs]
assert "Garmin" in labels and "Oura" in labels, labels
assert any("Client ID" in (ti.label or "") for ti in at.text_input), [ti.label for ti in at.text_input]
print("   Oura-formuläret finns:", [ti.label for ti in at.text_input])

# 2. Oura-anslutning + hämtning (utanför UI, samma funktioner som knappen kör)
import requests
real = oura.auth_url
def spy(cid, state):
    spy.url = real(cid, state); return spy.url
oura.auth_url = spy
threading.Thread(target=lambda: (time.sleep(0.3), requests.get(spy.url, timeout=10)), daemon=True).start()
oura.authorize("klient", "hemlig", timeout_s=10, open_browser=False)
assert jobs.source() == "oura"
assert jobs.start(M.N_DAYS + 1, full=True)
while jobs.JOB.running:
    time.sleep(0.2)
assert jobs.JOB.error is None, jobs.JOB.error
con = db.connect(); print("   kalla i meta:", db.meta_get(con, "kalla"), "| dygn:", con.execute("select count(*) from days").fetchone()); con.close()

at, errs = run("Oura med data")
assert not errs
assert [t.label for t in at.tabs][:5] == ["Träningsöversikt", "Stress och återhämtning", "Träningslabb", "Bidrag", "Data"], [t.label for t in at.tabs]
assert not at.sidebar.children, "sidopanelen ska vara borta (punkt 13)"
assert not at.metric, "inga nyckeltalsrutor (punkt 13)"
txt = " ".join(x.value for x in at.caption) + " ".join(x.value for x in at.markdown) + " ".join(x.value for x in at.success)
assert "Inloggad mot Oura" in txt, "statusen saknas"
assert "Oura-data" in txt, "Oura-noten i Dos–respons saknas"
print("   status och Oura-not visas, fem flikar, ingen sidopanel")

# 3. logga ut Oura, Garmin-exempeldata (regression)
oura.logout()
con = db.connect(); con.execute("delete from days"); con.execute("delete from activities"); con.commit()
import demo_data; demo_data.create(con); con.close()
at, errs = run("Garmin-exempeldata")
assert not errs

# 4. klocka utan Garmins träningslast (punkt 15): alla pass saknar last och TE, pulsen finns
con = db.connect(); con.execute("delete from days"); con.execute("delete from activities"); con.commit()
demo_data.create(con, utan_last=True); con.close()
at, errs = run("Garmin-exempeldata utan träningslast")
assert not errs
txt = " ".join(x.value for x in at.markdown) + " ".join(x.value for x in at.info)
assert "last räknad ur minuter och puls" in txt, "statusraden säger inte att lasten räknas"
assert "Din klocka ger inte Garmins träningslast" in txt, "texten i Stress och återhämtning saknas"
print("   statusrad och text om ersättningslasten visas")
shutil.rmtree(tmp, ignore_errors=True)
print("ALLT OK")
