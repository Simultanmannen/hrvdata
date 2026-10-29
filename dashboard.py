"""HRV-data: nattlig HRV, dos–respons och den långsamma formen. Körs med `streamlit run dashboard.py`."""
import json
import sqlite3

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

import berattelse
import bidrag
import db as dbm
import jobs
import labb
import modell
import oura
import passtyper
import planering
import sync
import updater
from config import BIDRAG_DIR, BIDRAG_MOTTAGARE, DATA_DIR, DB_PATH, ROOT
from version import UPDATE_REPO, VERSION

st.set_page_config(page_title="HRV-data", page_icon="💓", layout="wide")

# Färger i fast ordning (färgblindsäkra)
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
MUTED, INK2, GRID, INK = "#9CA3AF", "#6B7280", "#EEF0F4", "#111827"
ACCENT, ACCENT_SOFT = "#5B5BD6", "#E9EAF0"
C_HRV, C_7D, C_PRED, C_POS, C_NEG = SERIES[0], SERIES[6], SERIES[3], SERIES[2], SERIES[7]
C_LAST, C_STRESS, C_SJUK = SERIES[1], SERIES[4], SERIES[7]
FONT = 'Inter, "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif'
LAYOUT = dict(template="plotly_white", margin=dict(l=8, r=8, t=44, b=8),
              paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
              font=dict(family=FONT, size=12, color=INK2),
              title_font=dict(family=FONT, size=14, color=INK),
              hoverlabel=dict(bgcolor="white", bordercolor=GRID, font=dict(family=FONT, color=INK)),
              legend=dict(font=dict(size=11), orientation="h", y=1.02, yanchor="bottom", x=1, xanchor="right"),
              hovermode="x unified")
GRUPPNAMN = {"styrka": "Styrka", "inomhuscykel": "Inomhuscykel", "cykel": "Cykel", "lopning": "Löpning",
             "vandring": "Vandring/promenad", "skidor": "Skidor/skate", "paddling": "Paddling", "ovrigt": "Övrigt"}
GRUPPFARG = {g: SERIES[i] for i, g in enumerate(GRUPPNAMN)}

st.markdown(f"""<style>
html, body, [class*="css"], .stMarkdown, button, input, textarea {{ font-family: {FONT}; }}
.block-container {{ padding-top: 2rem; padding-bottom: 4rem; max-width: 1440px; }}
h1, h2, h3 {{ font-weight: 600 !important; letter-spacing: -0.02em; color: {INK}; }}
h4, h5 {{ font-weight: 500 !important; font-size: .9rem !important; color: {INK2}; margin-top: 1.2rem; }}
[data-testid="stMetric"] {{ background: #FFFFFF; border-radius: 14px; padding: 14px 18px 12px;
                           box-shadow: 0 1px 2px rgba(17,24,39,.04); }}
[data-testid="stMetricLabel"] p {{ font-size: .8rem; color: {INK2}; white-space: normal; overflow: visible; }}
[data-testid="stMetricLabel"] div {{ overflow: visible; }}
[data-testid="stMetricValue"] {{ font-weight: 600; font-size: 1.65rem; color: {INK}; letter-spacing: -0.02em; }}
[data-testid="stPlotlyChart"], [data-testid="stDataFrame"] {{ background: #FFFFFF; border-radius: 14px;
                           padding: 6px 8px; box-shadow: 0 1px 2px rgba(17,24,39,.04); }}
[data-testid="stVerticalBlockBorderWrapper"] {{ border-radius: 14px; }}
.stTabs [role="tablist"] {{ gap: 2px; background: {ACCENT_SOFT}; border-radius: 999px; padding: 4px;
                            width: fit-content; max-width: 100%; overflow-x: auto; border: none !important;
                            box-shadow: none !important; margin-bottom: 1rem; }}
.stTabs [role="tab"] {{ padding: .4rem 1.05rem !important; border-radius: 999px; font-size: .88rem; color: {INK2};
                        height: auto; border: none !important; box-shadow: none !important; }}
.stTabs [role="tab"] p {{ font-size: .88rem; }}
.stTabs [role="tab"][aria-selected="true"] {{ background: #FFFFFF; color: {INK} !important; font-weight: 500;
                                              box-shadow: 0 1px 2px rgba(17,24,39,.10) !important; }}
.stTabs [role="tablist"]::after, .stTabs [role="tab"] > div:not([data-testid]) {{ display: none !important; }}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {{ display: none; }}
.stButton button, .stDownloadButton button, .stFormSubmitButton button {{ border-radius: 10px; }}
[data-testid="stSidebar"] {{ border-right: 1px solid {GRID}; }}
.pd-brand {{ display: flex; align-items: center; gap: .65rem; margin-bottom: 1rem; }}
.pd-brand .mark {{ width: 28px; height: 28px; background: {ACCENT}; border-radius: 8px; display: inline-flex;
                   align-items: center; justify-content: center; color: #fff; font-weight: 600; font-size: .9rem; }}
.pd-brand .name {{ font-size: 1.3rem; font-weight: 600; letter-spacing: -0.02em; color: {INK}; }}
.pd-brand .sub {{ color: {INK2}; font-size: .85rem; margin-left: .35rem; padding: 2px 10px;
                  background: #FFFFFF; border-radius: 999px; }}
hr {{ border-color: {GRID} !important; }}
</style>""", unsafe_allow_html=True)


def brand(subtitle: str = "") -> None:
    sub = f'<span class="sub">{subtitle}</span>' if subtitle else ""
    st.markdown(f'<div class="pd-brand"><span class="mark">H</span><span class="name">HRV-data</span>'
                f'<span style="color:{MUTED};font-size:.8rem">v{VERSION}</span>{sub}</div>', unsafe_allow_html=True)


# ------------------------------------------------------------------ uppdateringar
@st.cache_data(ttl=3600, show_spinner=False)
def check_update():
    try:
        rel = updater.latest_release()
    except Exception:
        return None
    return rel if updater.is_newer(rel) else None


def install_update(rel: dict) -> None:
    import os
    import threading
    try:
        with st.spinner(f"Hämtar version {rel['version']} …"):
            result = updater.install(updater.download(rel["url"]))
    except Exception as e:
        st.error(f"Uppdateringen misslyckades ({type(e).__name__}). Inget har ändrats i dina data. Försök igen senare.")
        return
    if result["needs_full_restart"]:
        st.success(f"Version {rel['version']} är installerad. Stäng det svarta fönstret och starta appen igen "
                   "med Starta HRV-data – den här versionen behöver installera nya delar.")
        return
    st.success(f"Version {rel['version']} är installerad. Appen startar om – sidan laddas om av sig själv.")
    threading.Timer(1.5, lambda: os._exit(updater.RESTART_CODE)).start()


def update_banner() -> None:
    rel = check_update()
    if not rel or st.session_state.get("hide_update") == rel["version"]:
        return
    with st.container(border=True):
        c1, c2, c3 = st.columns([4, 1.6, 1.1], vertical_alignment="center")
        c1.markdown(f"**Ny version {rel['version']} finns** (du har {VERSION})")
        if rel["notes"]:
            c1.caption(rel["notes"][:400])
        if c2.button("Uppdatera nu", type="primary", key="do_update", disabled=jobs.JOB.running,
                     help="Programfilerna byts ut. Dina data och din inloggning ligger kvar."):
            install_update(rel)
        if c3.button("Senare", key="later_update"):
            st.session_state["hide_update"] = rel["version"]
            st.rerun()


def style(fig, height=340, **kw):
    fig.update_layout(height=height, **{**LAYOUT, **kw})
    fig.update_xaxes(gridcolor=GRID, zeroline=False, linecolor=GRID, ticks="")
    fig.update_yaxes(gridcolor=GRID, zeroline=False, linecolor=GRID, ticks="")
    return fig


MANADER = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]


def sv(ts, year=False) -> str:
    """Datum på svenska, t.ex. '20 okt' eller '20 okt 2026'."""
    return f"{ts.day} {MANADER[ts.month - 1]}" + (f" {ts.year}" if year else "")


def fmt(x, nd=1, sign=False, unit=""):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "–"
    s = f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"
    return s.replace(".", ",").replace("-", "−") + unit


def delta(x, nd=1, unit=""):
    """Förändringsvärde till st.metric. Streamlit läser pilens riktning ur första tecknet och känner
    bara igen ASCII-minus, så det typografiska minustecknet från fmt gav en uppåtpil på negativa värden."""
    return fmt(x, nd, True, unit).replace("−", "-", 1)


# ------------------------------------------------------------------ data
def db_version() -> float:
    files = [DB_PATH, DB_PATH.with_name(DB_PATH.name + "-wal")]
    return max((f.stat().st_mtime for f in files if f.exists()), default=0)


def query(sql, params=()) -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as con:
        return pd.read_sql(sql, con, params=params)


@st.cache_data(show_spinner=False)
def load_days(v) -> pd.DataFrame:
    df = query("SELECT * FROM days ORDER BY date")
    df["datum"] = pd.to_datetime(df["date"])
    df["sömn_h"] = df["sleep_seconds"] / 3600
    return df


@st.cache_data(show_spinner=False)
def load_acts(v) -> pd.DataFrame:
    df = query("SELECT * FROM activities ORDER BY start_local")
    df["datum"] = pd.to_datetime(df["date"])
    df["grupp"] = df["type_key"].map(modell.grupp)
    df["min"] = df["duration_s"].fillna(0) / 60
    return df


@st.cache_data(show_spinner=False)
def run_model(v) -> dict:
    with sqlite3.connect(DB_PATH) as con:
        return modell.kor(con)


@st.cache_data(show_spinner=False)
def passtyp_data(v) -> dict:
    """Passtyperna över alla pass (punkt 8 och 9). Sjukdygnen från modellen tas bort ur kostnaderna."""
    R = run_model(v)
    with sqlite3.connect(DB_PATH) as con:
        return passtyper.bygg(con, R.get("sjuk", []) if R.get("ok") else [])


@st.cache_resource(max_entries=1, show_spinner=False)
def planerare(v):
    R = run_model(v)
    if not R.get("ok"):
        return None
    with sqlite3.connect(DB_PATH) as con:
        return planering.Planerare(con, R)


@st.cache_data(show_spinner=False)
def referens() -> dict | None:
    p = ROOT / "referens.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def model_frame(R: dict) -> pd.DataFrame:
    """Modellens serier per NATT (rad d gäller natten d+1)."""
    natt = pd.to_datetime(R["dates"]) + pd.Timedelta(days=1)
    return pd.DataFrame({"datum": natt, "hrv": R["y"], "pred_bas": R["pred_base"],
                         "pred": R["pred_form"], "form": R["form"]})


# ------------------------------------------------------------------ hämtning
def start_sync(days_back: int, full: bool = False) -> None:
    if not jobs.start(days_back, full):
        st.toast("En hämtning pågår redan.")
    st.rerun()


def incremental_days(days: pd.DataFrame) -> int:
    """Hur långt bakåt en vanlig hämtning ska gå: till senast sparade dygn, plus marginal."""
    real = days[days["fetched_at"] != "demo"]
    if real.empty:
        return 30
    gap = (pd.Timestamp.today().normalize() - real["datum"].max()).days
    return int(max(14, gap + 5))


def refresh_now() -> None:
    st.session_state.pop("frozen_v", None)
    st.cache_data.clear()


def _job_body() -> None:
    j = jobs.JOB
    if j.running:
        with st.container(border=True):
            st.markdown(f"##### Hämtar från {jobs.source_name()}")
            if "login_note" in st.session_state:
                st.write(st.session_state["login_note"])
            phase = jobs.PHASES.get(j.phase, f"Ansluter till {jobs.source_name()}")
            if j.phase == "list":
                detail = f"{j.done} nya pass hittade hittills"
            elif j.total:
                detail = f"dygn {j.done} av högst {j.total}"
            else:
                detail = ""
            st.markdown(f"**{phase}**  ·  {detail}" if detail else f"**{phase} …**")
            st.progress(j.fraction or 0.0)
            st.caption("  ·  ".join(x for x in (j.eta_text(), "Du kan använda appen under tiden.",
                                                (j.lines[-1] if j.lines else "")) if x))
            if j.phase == "days" and st.button("Visa det som hämtats hittills", key="show_partial"):
                refresh_now()
                st.rerun(scope="app")
    elif not j.acknowledged:
        j.acknowledged = True
        st.session_state.pop("login_note", None)
        if j.error is None:
            st.session_state["job_done"] = f"Hämtningen är klar – {j.new} nya pass. Modellen räknas om automatiskt."
        elif "Authentication" in type(j.error).__name__:
            st.session_state["job_error"] = "Inloggningen mot Garmin har gått ut. Logga in igen under Data & konto."
        elif isinstance(j.error, oura.OuraAuthError):
            st.session_state["job_error"] = str(j.error)
        else:
            fe = oura.friendly_error if jobs.source() == "oura" else sync.friendly_error
            st.session_state["job_error"] = (fe(j.error) + " Klicka på hämta igen för att "
                                             "fortsätta där det slutade – redan hämtade dygn hoppas över.")
        refresh_now()
        st.rerun(scope="app")


@st.fragment(run_every=2)
def _job_panel_live() -> None:
    _job_body()


def job_panel() -> None:
    if jobs.JOB.running:
        _job_panel_live()
    else:
        _job_body()
    for key, show in (("job_error", st.error), ("job_done", st.success)):
        if key in st.session_state:
            c1, c2 = st.columns([6, 1], vertical_alignment="center")
            with c1:
                show(st.session_state[key])
            if c2.button("OK", key=f"{key}_ok"):
                st.session_state.pop(key)
                st.rerun()


def history_controls(button_label: str, key: str) -> None:
    options = {"Allt som har HRV (rekommenderas)": 3650, "Senaste 3 åren": 1095, "Senaste 2 åren": 730,
               "Senaste året": 365}
    choice = st.radio("Hur långt tillbaka?", list(options), index=0, key=f"{key}_range")
    st.caption("I bakgrunden, ungefär en sekund per dygn från Garmin och några minuter totalt från Oura. "
               "Hämtningen stannar av sig själv där klockan eller ringen slutade mäta HRV. Avbryts den "
               "fortsätter den där den slutade nästa gång.")
    if st.button(button_label, type="primary", disabled=jobs.JOB.running, key=f"{key}_go"):
        start_sync(options[choice], full=True)


def login_form() -> None:
    if "mfa" in st.session_state:
        st.info("Garmin har skickat en engångskod (sms, e-post eller i appen).")
        with st.form("mfa_form"):
            code = st.text_input("Engångskod")
            ok = st.form_submit_button("Bekräfta", type="primary")
        if ok:
            if not code.strip():
                st.warning("Skriv in koden från Garmin.")
                return
            api, state = st.session_state["mfa"]
            with st.spinner("Kontrollerar koden hos Garmin …"):
                try:
                    sync.finish_login(api, state, code.strip())
                except Exception as e:
                    st.error(sync.friendly_error(e) + " Kontrollera koden och försök igen.")
                    return
            st.session_state.pop("mfa", None)
            after_login()
        if st.button("Avbryt och börja om", key="mfa_cancel"):
            st.session_state.pop("mfa", None)
            st.rerun()
        return
    with st.form("login_form"):
        email = st.text_input("E-post (samma som i Garmin Connect)")
        pw = st.text_input("Lösenord", type="password")
        ok = st.form_submit_button("Logga in", type="primary")
    st.caption("Lösenordet sparas inte. Appen sparar bara en inloggningsnyckel här på datorn, "
               "och all data stannar på datorn.")
    if ok:
        if not email or not pw:
            st.warning("Fyll i både e-post och lösenord.")
            return
        with st.spinner("Loggar in hos Garmin …"):
            try:
                api, state = sync.start_login(email.strip(), pw)
            except Exception as e:
                st.error(sync.friendly_error(e))
                return
        if api is not None:
            st.session_state["mfa"] = (api, state)
            st.rerun()
        after_login()


def real_day_count() -> int:
    con = dbm.connect()
    n = con.execute("SELECT count(*) FROM days WHERE fetched_at != 'demo'").fetchone()[0]
    con.close()
    return n


def after_login() -> None:
    """Första gången: starta hela historikhämtningen direkt, så att det syns att något händer."""
    if real_day_count() == 0 and not jobs.JOB.running:
        jobs.start(3650, full=True)
        st.session_state["flash"] = "Inloggad! Hämtningen har startat."
        if jobs.source() == "oura":
            st.session_state["login_note"] = (
                "Oura är anslutet. Appen hämtar nu alla dygn som har HRV, från idag och bakåt, 30 dygn åt "
                "gången. Det tar några minuter första gången. Du kan titta på det som kommit hittills under "
                "tiden, men modellen behöver minst ett halvår.")
        else:
            st.session_state["login_note"] = (
                "Inloggad mot Garmin Connect. Appen hämtar nu alla dygn som har HRV, från idag och bakåt. "
                "Det tar ungefär en sekund per dygn, alltså 15–40 minuter första gången. Du kan titta på "
                "det som kommit hittills under tiden, men modellen behöver minst ett halvår.")
    else:
        st.session_state["flash"] = f"Inloggad mot {jobs.source_name()}."
    st.rerun()


def oura_form() -> None:
    st.markdown(
        f"Oura kräver en egen **API-registrering** i ditt Oura-konto (gratis, en gång):\n\n"
        f"1. Gå till [{oura.APP_REG_URL}]({oura.APP_REG_URL}) och logga in med ditt Oura-konto.\n"
        f"2. Skapa en ny application. Namn: valfritt (t.ex. *HRV-data*). "
        f"Redirect URI: `{oura.REDIRECT_URI}` (exakt så).\n"
        f"3. Kopiera **Client ID** och **Client Secret** hit och klicka *Anslut Oura*. "
        f"Webbläsaren öppnar Oura, där du godkänner en gång.")
    with st.form("oura_form"):
        cid = st.text_input("Client ID")
        sec = st.text_input("Client Secret", type="password")
        ok = st.form_submit_button("Anslut Oura", type="primary")
    st.caption("Client Secret och den token Oura ger sparas i mappen data på den här datorn och skickas "
               "bara till Oura. Inget lösenord passerar appen.")
    if ok:
        with st.spinner("Väntar på att du godkänner i webbläsaren (högst 4 minuter) …"):
            try:
                oura.authorize(cid, sec)
            except Exception as e:
                st.error(oura.friendly_error(e))
                return
        after_login()


def data_page(has_data: bool) -> None:
    c1, c2 = st.columns(2, gap="large")
    with c1:
        src = jobs.source()
        st.subheader("Oura" if src == "oura" else "Garmin Connect")
        if src:
            st.success(f"Inloggad mot {jobs.source_name()}")
            if has_data and real_day_count() > 0:
                d = load_days(db_version())
                real = d[d["fetched_at"] != "demo"]
                st.caption(f"{len(real)} dygn sparade, {real['date'].min()} – {real['date'].max()}, "
                           f"varav {real['hrv_night'].notna().sum()} med HRV.")
                if st.button("Hämta nya data", type="primary", disabled=jobs.JOB.running):
                    start_sync(incremental_days(d))
                with st.expander("Hämta eller komplettera historik"):
                    history_controls("Hämta historik", "hist")
            else:
                st.markdown("##### Steg 2 – hämta din historik")
                history_controls("Hämta mina data", "first")
            if st.button("Logga ut", disabled=jobs.JOB.running):
                (oura.logout if src == "oura" else sync.logout)()
                st.rerun()
        else:
            if not has_data:
                st.markdown("##### Steg 1 – logga in")
            tg, to = st.tabs(["Garmin", "Oura"])
            with tg:
                login_form()
            with to:
                oura_form()
    with c2:
        st.subheader("Befintlig databas")
        st.caption("Har du redan en garmin.db, från Garmin-kitet eller ett eget hämtningsskript? Läs in den här "
                   "i stället för att hämta allt igen. Sedan räcker det att hämta det som är nytt.")
        fil = st.file_uploader("Välj garmin.db", type=["db", "sqlite"], key="import_db")
        if fil is not None and st.button("Läs in databasen", type="primary"):
            import importera
            tmp = DATA_DIR / "_import.db"
            tmp.write_bytes(fil.getvalue())
            con = dbm.connect()
            try:
                with st.spinner("Läser in …"):
                    n_d, n_a = importera.importera(tmp, con)
            except Exception as e:
                st.error(f"Kunde inte läsa filen: {e}")
            else:
                st.session_state["flash"] = f"Inläst: {n_d} dygn och {n_a} pass."
                st.cache_data.clear()
                st.cache_resource.clear()
                st.rerun()
            finally:
                con.close()
                tmp.unlink(missing_ok=True)
        st.subheader("Exempeldata")
        st.caption("En påhittad person med känd dos–respons, för att se hur appen fungerar utan Garmin-konto. "
                   "Tas bort automatiskt när du hämtar dina egna data.")
        con = dbm.connect()
        has_demo = con.execute("SELECT 1 FROM days WHERE fetched_at='demo' LIMIT 1").fetchone()
        con.close()
        if has_demo:
            if st.button("Ta bort exempeldata"):
                con = dbm.connect()
                sync.remove_demo(con)
                con.close()
                st.cache_data.clear()
                st.rerun()
        elif real_day_count() == 0 and st.button("Prova med exempeldata"):
            import demo_data
            with st.spinner("Skapar exempeldata …"):
                con = dbm.connect()
                demo_data.create(con)
                con.close()
            st.cache_data.clear()
            st.rerun()
    st.divider()
    st.caption(f"All data sparas i `{DATA_DIR}`. Kopiera mappen för att ta en säkerhetskopia.")
    st.subheader("Om appen")
    c1, c2 = st.columns([3, 1])
    if UPDATE_REPO:
        c1.caption(f"HRV-data version {VERSION}, modellversion {modell.MODELLVERSION}. "
                   f"Nya versioner hämtas från github.com/{UPDATE_REPO}.")
        if c2.button("Sök efter uppdatering", key="check_update_now"):
            check_update.clear()
            st.session_state.pop("hide_update", None)
            if check_update() is None:
                st.toast(f"Du har den senaste versionen ({VERSION}).")
            else:
                st.rerun()
    else:
        c1.caption(f"HRV-data version {VERSION}, modellversion {modell.MODELLVERSION}. "
                   "Automatiska uppdateringar är inte inställda.")


# ------------------------------------------------------------------ diagram
def line_chart(df, col, title, color, unit="", roll=7, height=280):
    fig = go.Figure()
    s = df.set_index("datum")[col]
    fig.add_scatter(x=s.index, y=s, mode="markers", marker=dict(size=4, color=color, opacity=.35),
                    name="dygn", hovertemplate=f"%{{y:.1f}}{unit}<extra>dygn</extra>")
    if roll and s.notna().sum() > roll:
        r = s.rolling(roll, min_periods=max(2, roll // 2)).mean()
        fig.add_scatter(x=r.index, y=r, mode="lines", line=dict(color=color, width=2),
                        name=f"{roll} dygn", hovertemplate=f"%{{y:.1f}}{unit}<extra>{roll} dygn</extra>")
    return style(fig, height, title=title, showlegend=False)


def hrv_chart(days, mf, height=380):
    fig = go.Figure()
    fig.add_scatter(x=days["datum"], y=days["hrv_night"], mode="markers", name="Natt",
                    marker=dict(size=5, color=C_HRV, opacity=.55), hovertemplate="%{y:.0f} ms<extra>natt</extra>")
    fig.add_scatter(x=days["datum"], y=days["hrv_7d"], mode="lines", name="7-dygnssnitt",
                    line=dict(color=C_7D, width=2), hovertemplate="%{y:.0f} ms<extra>7 dygn</extra>")
    if mf is not None and not mf.empty:
        fig.add_scatter(x=mf["datum"], y=mf["pred"], mode="lines", name="Modellens förväntan",
                        line=dict(color=C_PRED, width=1.5, dash="dot"),
                        hovertemplate="%{y:.0f} ms<extra>förväntat</extra>")
    return style(fig, height, title="Nattlig HRV (ms)")


def vilopuls_chart(days, mf, height=380):
    """Vilopulsvägen (punkt 10b): samma diagram som hrv_chart, med vilopulsen och dess 7-dygnsmedel."""
    fig = go.Figure()
    fig.add_scatter(x=days["datum"], y=days["rhr"], mode="markers", name="Natt",
                    marker=dict(size=5, color=C_HRV, opacity=.55),
                    hovertemplate="%{y:.0f} slag/min<extra>natt</extra>")
    fig.add_scatter(x=days["datum"], y=days["rhr_7d"], mode="lines", name="7-dygnsmedel",
                    line=dict(color=C_7D, width=2), hovertemplate="%{y:.1f} slag/min<extra>7 dygn</extra>")
    if mf is not None and not mf.empty:
        fig.add_scatter(x=mf["datum"], y=mf["pred"], mode="lines", name="Modellens förväntan",
                        line=dict(color=C_PRED, width=1.5, dash="dot"),
                        hovertemplate="%{y:.1f} slag/min<extra>förväntat</extra>")
    return style(fig, height, title="Vilopuls i sömnen (slag/min)")


def form_chart(mf, height=300, title=None):
    # Vilopulsvägen: formen är i slag/min och positivt är sämre, så färgerna byter plats
    enh = ENH.strip()
    title = title or f"Långsam form ({enh})"
    upp, ned = (C_NEG, C_POS) if VP else (C_POS, C_NEG)
    fupp, fned = ("rgba(227,73,72,.30)", "rgba(27,175,122,.35)") if VP else ("rgba(27,175,122,.35)", "rgba(227,73,72,.30)")
    fig = go.Figure()
    pos, neg = mf["form"].clip(lower=0), mf["form"].clip(upper=0)
    fig.add_scatter(x=mf["datum"], y=pos, fill="tozeroy", mode="lines", line=dict(width=0, color=upp),
                    fillcolor=fupp, hoverinfo="skip", showlegend=False)
    fig.add_scatter(x=mf["datum"], y=neg, fill="tozeroy", mode="lines", line=dict(width=0, color=ned),
                    fillcolor=fned, hoverinfo="skip", showlegend=False)
    fig.add_scatter(x=mf["datum"], y=mf["form"], mode="lines", line=dict(color=INK2, width=1.2),
                    hovertemplate=f"%{{y:+.1f}} {enh}<extra>form</extra>", showlegend=False)
    fig.add_hline(y=0, line_color=MUTED, line_width=1)
    return style(fig, height, title=title)


# ------------------------------------------------------------------ start
dbm.connect().close()
if jobs.JOB.running:
    v = st.session_state.setdefault("frozen_v", db_version())
else:
    st.session_state.pop("frozen_v", None)
    v = db_version()
days_all = load_days(v)
if "flash" in st.session_state:
    st.toast(st.session_state.pop("flash"))

if days_all.empty:
    brand("Återhämtning och träning")
    update_banner()
    st.markdown("## Välkommen")
    st.write("Appen hämtar din nattliga HRV, sömn, stress och dina pass från Garmin Connect eller Oura och sparar dem här "
             "på datorn. Sedan räknar den ut hur din kropp svarar på träning och vardagsstress, och hur mycket "
             "av din återhämtning som beror på ett långsamt tillstånd som klockan själv inte ser.")
    job_panel()
    data_page(has_data=False)
    st.stop()

acts_all = load_acts(v)
is_demo = (days_all["fetched_at"] == "demo").any()
with st.spinner("Räknar modellen …"):
    R = run_model(v)
mf_all = model_frame(R) if R.get("ok") else None
# Vilopulsvägen (punkt 10b): klockan saknar HRV, utfallet är vilopulsen i slag/min och högre är sämre
VP = bool(R.get("ok")) and R.get("utfall") == "vilopuls"
ENH = " slag/min" if VP else " ms"
UTF = "vilopulsen" if VP else "HRV"
if VP:
    _r7 = modell.vilopuls_7d({r.date: {"rhr": None if pd.isna(r.rhr) else r.rhr} for r in days_all.itertuples()})
    days_all["rhr_7d"] = days_all["date"].map(_r7)
_con = dbm.connect()
data_source = dbm.meta_get(_con, "kalla") or jobs.source()
_con.close()


def figurtext(*stycken: str) -> None:
    """Fast text direkt under en figur (punkt 13: två till fyra meningar, inte i expander)."""
    for s in stycken:
        if s:
            st.markdown(s)


def statusrad() -> None:
    """Ersätter sidopanelen: källa, senaste hämtning, antal nätter, knappen, och varningar i samma rad."""
    src = jobs.source()
    namn = {"garmin": "Garmin", "oura": "Oura"}.get(src or data_source, "Exempeldata" if is_demo else "Ingen källa")
    real = days_all[days_all["fetched_at"] != "demo"]
    senast = pd.to_datetime(real["fetched_at"].dropna().str[:10], errors="coerce").max() if not real.empty else None
    n_natter = int(days_all["rhr" if VP else "hrv_night"].notna().sum())
    c1, c2, c3 = st.columns([5, 1.6, 3], vertical_alignment="center")
    delar = [f"**{namn}**"]
    if senast is not None and pd.notna(senast):
        delar.append(f"senast hämtat {sv(senast)}")
    delar.append(f"{n_natter:,} nätter".replace(",", " "))
    c1.markdown(" · ".join(delar))
    klick = c2.button("Hämta nya data", type="primary", disabled=jobs.JOB.running or not src, width="stretch",
                      key="status_hamta")
    with c3:
        if not src:
            st.markdown(f":orange[Inte inloggad. Logga in under fliken **Data**.]")
        rel = check_update()
        if rel and st.session_state.get("hide_update") != rel["version"]:
            u1, u2 = st.columns([2, 1], vertical_alignment="center")
            u1.markdown(f":orange[Ny version {rel['version']} finns.]")
            if u2.button("Uppdatera", key="do_update", disabled=jobs.JOB.running,
                         help="Programfilerna byts ut. Dina data och din inloggning ligger kvar."):
                install_update(rel)
    if klick:
        start_sync(incremental_days(days_all))


brand("Exempeldata" if is_demo else "")
statusrad()
job_panel()

tab_a, tab_b, tab_c, tab_d, tab_e = st.tabs(
    ["Träningsöversikt", "Stress och återhämtning", "Träningslabb", "Bidrag", "Data"])

# ------------------------------------------------------------------ a) träningsöversikt
PERIODER = {"2 v": 14, "4 v": 28, "3 mån": 91, "6 mån": 182, "1 år": 365, "Allt": None, "Eget": "eget"}

with tab_a:
    sista = max(days_all["datum"].max(), acts_all["datum"].max() if not acts_all.empty else days_all["datum"].max())
    forsta = days_all["datum"].min()
    c1, c2, c3 = st.columns([3.3, 2.2, 3.2], vertical_alignment="bottom")
    pval = c1.segmented_control("Period", list(PERIODER), default="3 mån", key="a_period") or "3 mån"
    if PERIODER[pval] == "eget":
        intervall = c2.date_input("Från och till", (max(forsta, sista - pd.Timedelta(days=90)).date(), sista.date()),
                                  min_value=forsta.date(), max_value=sista.date(), key="a_datum")
        d0, d1 = (pd.Timestamp(intervall[0]), pd.Timestamp(intervall[1])) if len(intervall) == 2 else (forsta, sista)
    else:
        d1 = sista
        d0 = forsta if PERIODER[pval] is None else max(forsta, sista - pd.Timedelta(days=PERIODER[pval] - 1))
        c2.markdown(f"{sv(d0, True)} – {sv(d1, True)}")
    sporter = [g for g in GRUPPNAMN if g in set(acts_all["grupp"])]
    valda = c3.multiselect("Sport", sporter, format_func=GRUPPNAMN.get, placeholder="Alla sporter", key="a_sport")
    valda = valda or sporter
    dagar_p = days_all[(days_all["datum"] >= d0) & (days_all["datum"] <= d1)]
    pass_p = acts_all[(acts_all["datum"] >= d0) & (acts_all["datum"] <= d1) & acts_all["grupp"].isin(valda)]
    vecka = (d1 - d0).days > 200

    # --- remsan: pass överst, nätter under
    niva = "rhr_7d" if VP else "hrv_7d"
    natt = dagar_p.dropna(subset=["rhr" if VP else "hrv_night"]).copy()
    natt["avv"] = natt["rhr" if VP else "hrv_night"] - natt[niva]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.55, 0.45], vertical_spacing=0.07)
    if vecka:
        pw = pass_p.assign(vecka=pass_p["datum"].dt.to_period("W-SUN").dt.start_time)
        wl = pw.pivot_table(index="vecka", columns="grupp", values="training_load", aggfunc="sum", fill_value=0)
        for g in [g for g in GRUPPNAMN if g in wl.columns]:
            fig.add_bar(x=wl.index, y=wl[g], name=GRUPPNAMN[g], marker_color=GRUPPFARG[g], row=1, col=1,
                        hovertemplate="vecka %{x|%d %b}: last %{y:.0f}<extra>" + GRUPPNAMN[g] + "</extra>")
        nw = natt.assign(vecka=natt["datum"].dt.to_period("W-SUN").dt.start_time).groupby("vecka")["avv"].mean()
        fig.add_scatter(x=nw.index, y=nw, mode="lines+markers", line=dict(color=C_HRV, width=1.5), row=2, col=1,
                        marker=dict(size=5), showlegend=False,
                        hovertemplate="vecka %{x|%d %b}: %{y:+.1f}" + ENH + "<extra>nätter</extra>")
    else:
        for g in [g for g in GRUPPNAMN if g in set(pass_p["grupp"])]:
            sub = pass_p[pass_p["grupp"] == g]
            start_t = pd.to_datetime(sub["start_local"], errors="coerce").dt.strftime("%H:%M").fillna("–")
            cd = np.c_[sub["name"].fillna(GRUPPNAMN[g]), sub["min"].round(0), start_t, sub["avg_hr"].fillna(0).round(0),
                       sub["training_load"].fillna(0).round(0)]
            med = sub["training_load"].fillna(0) > 0
            fig.add_bar(x=sub["datum"][med], y=sub["training_load"][med], name=GRUPPNAMN[g], marker_color=GRUPPFARG[g],
                        customdata=cd[med.values], row=1, col=1,
                        hovertemplate="%{customdata[0]}<br>%{customdata[1]} min · start %{customdata[2]} · "
                                      "puls %{customdata[3]} · last %{customdata[4]}<extra></extra>")
            if (~med).any():  # pass utan last (vandring, yoga) som tunn markering
                topp = max(1.0, float(pass_p["training_load"].max() or 1))
                fig.add_scatter(x=sub["datum"][~med], y=[0.04 * topp] * int((~med).sum()), mode="markers", row=1, col=1,
                                marker=dict(symbol="line-ns-open", size=16, color=GRUPPFARG[g], line=dict(width=2)),
                                customdata=cd[(~med).values], showlegend=False,
                                hovertemplate="%{customdata[0]} (ingen last)<br>%{customdata[1]} min · start "
                                              "%{customdata[2]}<extra></extra>")
        farg = np.where(natt["avv"] >= 0, C_NEG if VP else C_POS, C_POS if VP else C_NEG)
        cd = np.c_[natt["hrv_night"].fillna(0).round(0), natt["sömn_h"].fillna(0).round(1), natt["rhr"].fillna(0).round(0)]
        fig.add_scatter(x=natt["datum"], y=natt["avv"], mode="markers", marker=dict(size=7, color=farg), row=2, col=1,
                        customdata=cd, showlegend=False,
                        hovertemplate=("%{x|%d %b}: %{y:+.1f}" + ENH + " mot 7 dygn<br>"
                                       + ("" if VP else "HRV %{customdata[0]} ms · ")
                                       + "sömn %{customdata[1]} h · vilopuls %{customdata[2]}<extra>natten</extra>"))
    fig.add_hline(y=0, line_color=MUTED, line_width=1, row=2, col=1)
    style(fig, 470, barmode="stack", title="Pass och nätter" + (" per vecka" if vecka else ""), hovermode="closest")
    fig.update_yaxes(title_text="Last", row=1, col=1)
    fig.update_yaxes(title_text=f"Natten mot egen nivå ({ENH.strip()})", row=2, col=1)
    st.plotly_chart(fig, width="stretch")
    figurtext(
        "Överst är dina pass, höjden är klockans träningslast och färgen sporten. Pass som klockan inte ger någon "
        "last, som vandring och yoga, syns som tunna streck. Under är dina nätter, som avvikelse från klockans egen "
        "7-dagarsnivå: " + ("över noll betyder högre vilopuls än vanligt, alltså en tyngre natt."
                            if VP else "under noll betyder en natt med sämre återhämtning än vanligt.")
        + (" Vid långa perioder visas veckor i stället för dagar." if vecka else
           " Håll muspekaren över en punkt för sömn och vilopuls."))

    # --- senaste passen
    st.markdown("#### Senaste passen")
    if pass_p.empty:
        st.info("Inga pass i vald period och sport.")
    else:
        sen = pass_p.sort_values("start_local", ascending=False).head(10)
        st.dataframe(pd.DataFrame({
            "Datum": sen["datum"].dt.strftime("%Y-%m-%d"), "Sport": sen["grupp"].map(GRUPPNAMN),
            "Namn": sen["name"], "Längd (min)": sen["min"].round(0).astype(int),
            "Last": sen["training_load"].round(0), "Start": pd.to_datetime(sen["start_local"], errors="coerce").dt.strftime("%H:%M")}),
            hide_index=True, width="stretch")

        # --- last per vecka
        pw = pass_p.assign(vecka=pass_p["datum"].dt.to_period("W-SUN").dt.start_time)
        wl = pw.pivot_table(index="vecka", columns="grupp", values="training_load", aggfunc="sum", fill_value=0)
        alla_v = pd.date_range(pd.Timestamp(d0).to_period("W-SUN").start_time, d1, freq="W-MON")
        wl = wl.reindex(alla_v, fill_value=0)
        fig = go.Figure()
        for g in [g for g in GRUPPNAMN if g in wl.columns]:
            fig.add_scatter(x=wl.index, y=wl[g], mode="lines+markers", name=GRUPPNAMN[g],
                            line=dict(color=GRUPPFARG[g], width=2), marker=dict(size=5),
                            hovertemplate="vecka %{x|%d %b}: %{y:.0f}<extra>" + GRUPPNAMN[g] + "</extra>")
        style(fig, 300, title="Last per vecka och sport", yaxis_title="Last per vecka")
        st.plotly_chart(fig, width="stretch")
        figurtext("Summan av klockans träningslast per vecka, en linje per sport. Den visar om träningen har "
                  "varit jämn eller kommit i klumpar, och vilken sport som bär veckorna.")

    # --- passtyper
    st.markdown("#### Dina passtyper")
    PT = passtyp_data(v)
    if not PT["ok"]:
        st.info(PT["skal"])
    else:
        farg = st.radio("Färga efter", ["Passtyp", "Sport", "Veckodag", "Månad", "Säsong", "År"], horizontal=True,
                        key="pt_farg")
        kl_namn = {j: f"Typ {j + 1}: {n}" for j, n in PT["namn"].items()}
        pdf = pd.DataFrame(PT["pass"])
        pdf["pc1"], pdf["pc2"] = PT["poang"][:, 0], PT["poang"][:, 1]
        dts = pd.to_datetime(pdf["date"])
        urval = ((dts >= d0) & (dts <= d1) & pdf["grupp"].isin(valda)).values
        kategori = {
            "Passtyp": (pdf["kluster"].map(kl_namn), [kl_namn[k] for k in sorted(kl_namn)]),
            "Sport": (pdf["grupp"].map(GRUPPNAMN), [GRUPPNAMN[g] for g in GRUPPNAMN if g in set(pdf["grupp"])]),
            "Veckodag": (dts.dt.weekday.map(lambda i: passtyper.VECKODAGAR[i]), passtyper.VECKODAGAR),
            "Månad": (dts.dt.month.map(lambda m: MANADER[m - 1]), MANADER),
            "Säsong": (dts.dt.month.map(passtyper.SASONG), ["vinter", "vår", "sommar", "höst"]),
            "År": (dts.dt.year.astype(str), sorted(dts.dt.year.astype(str).unique())),
        }
        vals, ordning = kategori[farg]
        if farg == "Månad":     # cyklisk skala: december och januari ligger nära varandra
            palett = px.colors.sample_colorscale("Twilight", [i / 12 for i in range(12)])
        elif farg == "År":      # ordnad skala: gammalt blekt, nytt mörkt
            palett = px.colors.sample_colorscale("Viridis", [i / max(1, len(ordning) - 1) for i in range(len(ordning))])
        else:
            palett = SERIES + [MUTED, "#8c564b", "#17becf"]
        fig = go.Figure()
        for i, kat in enumerate(ordning):
            m = (vals == kat).values & urval
            if not m.any():
                continue
            sub = pdf[m]
            fig.add_scatter(
                x=sub["pc1"], y=sub["pc2"], mode="markers", name=f"{kat} ({m.sum()})",
                marker=dict(size=6, color=palett[i % len(palett)], opacity=.7, line=dict(width=0)),
                customdata=np.c_[sub["date"], sub["typ"], sub["min"].round(0), sub["load"].round(0)],
                hovertemplate="%{customdata[0]} · %{customdata[1]}<br>%{customdata[2]} min · last %{customdata[3]}"
                              "<extra></extra>")
        style(fig, 460, hovermode="closest", title=f"Dina pass ({int(urval.sum())} av {len(pdf)})",
              xaxis_title=f"Huvudaxel 1: {passtyper.pc_namn(PT, 0)}", yaxis_title=f"Huvudaxel 2: {passtyper.pc_namn(PT, 1)}",
              legend=dict(orientation="v", x=1.01, y=1, xanchor="left", yanchor="top"))
        st.plotly_chart(fig, width="stretch")
        figurtext(
            "**Vad bilden visar.** Varje punkt är ett pass. Passets plats bestäms av hur det såg ut: längd, last, "
            "intensitet, puls och starttid. Pass som ligger nära varandra liknar varandra, och grupperna är dina egna "
            "passtyper, räknade ur din historik, inte Garmins kategorier. Axlarna har inga enheter, de är de två "
            "riktningar som skiljer dina pass mest åt, hos de flesta är den vågräta ungefär hur hårt och den lodräta "
            "kort kväll mot lång dag.",
            "**Hur man använder den.** Byt färg till år för att se om dina passtyper flyttat sig, till veckodag för att "
            "se om vissa typer bara händer på helger, till månad för säsongen. Tabellen under visar vad varje passtyp "
            "kostar natten efter jämfört med en vilonatt, det är modellen i nästa flik som räknar det. Om grupperna "
            "glider ihop (låg silhuett) är dina pass mer ett kontinuum än typer, och då är tabellens ordning det "
            "intressanta, inte gränserna.")
        if max(PT["silhuett"].values()) < 0.25:
            st.caption(f"Silhuett {fmt(max(PT['silhuett'].values()), 2)}: dina pass glider ihop, läs tabellens ordning.")
        rader = []
        for k in passtyper.sammanfatta(PT, urval):
            rader.append({
                "Typ": kl_namn[k["id"]], "Pass": k["n"],
                "Vanligast": ", ".join(f"{GRUPPNAMN.get(g, g)} ({n})" for g, n in k["topp"]),
                "Längd (median, min)": round(k["median_min"]),
                "Last (median)": None if k["median_last"] is None else round(k["median_last"]),
                "Kostnad natten efter mot vilonatt": "–" if k["kostnad"] is None else
                f"{fmt(k['kostnad'], 1, True, ' ms')} (± {fmt(1.96 * k['kostnad_se'], 1)})",
                "Dygn med bara den typen": k["n_enkla"]})
        tabell = pd.DataFrame(rader)
        if VP and not tabell.empty:  # kostnaden per passtyp räknas på HRV, som klockan inte har
            tabell = tabell.drop(columns="Kostnad natten efter mot vilonatt")
        st.dataframe(tabell, hide_index=True, width="stretch")
        if not VP:
            figurtext("Kostnaden är HRV natten efter passet minus 7-dygnsnivån, jämfört med samma sak efter en "
                      "vilonatt. Bara dygn med ett enda pass räknas, och störda nätter är borttagna. ± är ett "
                      "95 %-intervall.")


# ------------------------------------------------------------------ figurer för b, c och d
KLASSER = [(0, 0, "0"), (1, 50, "1–50"), (50, 100, "50–100"), (100, 150, "100–150"), (150, 200, "150–200"),
           (200, 300, "200–300"), (300, 10 ** 9, "300+")]


def dos_serier(R):
    """Dygnsvisa serier ur modellraderna: nattens avvikelse från egen nivå och dess förklaringar."""
    rows = R["rows"]
    niva_k = "r7" if VP else "h7"
    g = lambda k: np.array([r[k] for r in rows], float)
    return {"avv": g("y") - g(niva_k), "frisk": g("sjuk") == 0, "load": g("load"), "pload": g("pload"),
            "stress": g("stress"), "pstress": g("pstress"), "datum": [r["date"] for r in rows], "sjuk": g("sjuk")}


def klassmedel(avv, x, mask, klasser):
    ut = []
    for lo, hi, lab in klasser:
        m = mask & ((x == 0) if hi == 0 else ((x >= lo) & (x < hi)))
        if m.sum() >= 5:
            ut.append({"lab": lab, "y": float(avv[m].mean()), "se": float(avv[m].std(ddof=1) / np.sqrt(m.sum())),
                       "n": int(m.sum()), "x": float(np.median(x[m]))})
    return ut


def fig_dosrespons(R):
    S = dos_serier(R)
    kt, kd = R["koef_total"], R["koef"]
    fig = make_subplots(rows=1, cols=2, horizontal_spacing=0.1,
                        subplot_titles=("Träningslast", "Dagsstress (kvintiler)"))
    for namn, xk, b, farg in (("natten efter", "load", kt["load"]["b"], C_HRV),
                              ("natten därpå", "pload", kt["pload"]["b"], C_7D)):
        K = klassmedel(S["avv"], S[xk], S["frisk"], KLASSER)
        if not K:
            continue
        bas = K[0]["y"] if K[0]["lab"] == "0" else 0.0
        fig.add_scatter(x=[k["lab"] for k in K], y=[k["y"] for k in K], mode="markers", name=namn,
                        marker=dict(size=9, color=farg), error_y=dict(type="data", array=[1.96 * k["se"] for k in K],
                                                                       color=farg, thickness=1),
                        customdata=[k["n"] for k in K], row=1, col=1,
                        hovertemplate="%{y:+.1f}" + ENH + " · %{customdata} nätter<extra>" + namn + "</extra>")
        fig.add_scatter(x=[k["lab"] for k in K], y=[bas + b * k["x"] for k in K], mode="lines", showlegend=False,
                        line=dict(color=farg, dash="dot", width=1.5), row=1, col=1, hoverinfo="skip")
    q = np.percentile(S["stress"][S["frisk"]], [0, 20, 40, 60, 80, 100])
    for namn, xk, b, farg in (("natten efter", "stress", kd["stress"]["b"], C_HRV),
                              ("natten därpå", "pstress", kd["pstress"]["b"], C_7D)):
        x = S[xk]
        K = []
        for i in range(5):
            m = S["frisk"] & (x >= q[i]) & ((x <= q[i + 1]) if i == 4 else (x < q[i + 1]))
            if m.sum() >= 5:
                K.append({"x": float(x[m].mean()), "y": float(S["avv"][m].mean()),
                          "se": float(S["avv"][m].std(ddof=1) / np.sqrt(m.sum())), "n": int(m.sum())})
        if not K:
            continue
        mx, my = np.mean([k["x"] for k in K]), np.mean([k["y"] for k in K])
        fig.add_scatter(x=[k["x"] for k in K], y=[k["y"] for k in K], mode="markers", showlegend=False,
                        marker=dict(size=9, color=farg), error_y=dict(type="data", array=[1.96 * k["se"] for k in K],
                                                                       color=farg, thickness=1),
                        customdata=[k["n"] for k in K], row=1, col=2,
                        hovertemplate="stress %{x:.0f}: %{y:+.1f}" + ENH + " · %{customdata} nätter<extra>" + namn +
                                      "</extra>")
        fig.add_scatter(x=[k["x"] for k in K], y=[my + b * (k["x"] - mx) for k in K], mode="lines",
                        showlegend=False, line=dict(color=farg, dash="dot", width=1.5), row=1, col=2,
                        hoverinfo="skip")
    for c in (1, 2):
        fig.add_hline(y=0, line_color=MUTED, line_width=1, row=1, col=c)
    style(fig, 360, hovermode="closest")
    fig.update_yaxes(title_text=f"Natten mot egen nivå ({ENH.strip()})", row=1, col=1)
    fig.update_xaxes(title_text="Dagens last", row=1, col=1)
    fig.update_xaxes(title_text="Dagsstress", row=1, col=2)
    return fig


def kostnadsposter(R):
    """(etikett, värde, färg, klartext). Värdet i ms (HRV, minus = kostnad) eller slag/min (plus = kostnad)."""
    kd, kt, s, tp = R["koef"], R["koef_total"], R["beskrivning"], R["tungt_pass"]
    e = ENH
    poster = []
    if tp:
        poster.append((f"Tungt pass (last ≈ {tp['medellast']:.0f}), natten efter", tp["natt1"], C_LAST,
                       f"Ett av dina tyngsta pass ger {fmt(tp['natt1'], 1, True, e)} natten efter, jämfört med en "
                       "vilonatt."))
        if tp["natt2"] is not None:
            poster.append(("Samma pass, natten därpå", tp["natt2"], C_LAST,
                           f"Natten därpå ligger du {fmt(tp['natt2'], 1, True, e)} mot en vilonatt."))
    poster.append((f"Stressig dag (+{s['stress_sd']:.0f} enheter)", kd["stress"]["b"] * s["stress_sd"], C_STRESS,
                   f"En dag med stress en standardavvikelse över din vanliga ger {fmt(kd['stress']['b'] * s['stress_sd'], 1, True, e)} "
                   "samma natt. Talet är en prognos från klockans dagsmedel, som också räknar stressen under sömnen "
                   "fram till uppvaknandet, så en del av det är föregående natts tillstånd."))
    poster.append(("Störd natt (sjukflagga)", kt["sjuk"]["b"], C_SJUK,
                   f"Nätter då vilopuls och andning ligger över din egen nivå ger {fmt(kt['sjuk']['b'], 1, True, e)}."))
    if "smin" in kt and kt["smin"]["se"] and abs(kt["smin"]["b"] / kt["smin"]["se"]) > 2:
        poster.append(("Styrkepass 45 min", kt["smin"]["b"] * 45, C_POS,
                       f"Ett styrkepass på 45 minuter ger {fmt(kt['smin']['b'] * 45, 1, True, e)} utöver klockans last."))
    return poster


def fig_kostnader(poster):
    poster = sorted(poster, key=lambda p: p[1], reverse=VP)
    fig = go.Figure(go.Bar(y=[p[0] for p in poster], x=[p[1] for p in poster], orientation="h",
                           marker_color=[p[2] for p in poster], text=[fmt(p[1], 1, True) for p in poster],
                           textposition="outside", hovertemplate="%{x:+.1f}" + ENH + "<extra></extra>"))
    fig.add_vline(x=0, line_color=MUTED, line_width=1)
    xs = [p[1] for p in poster]
    lo, hi = min(0.0, min(xs)), max(0.0, max(xs))
    style(fig, 80 + 48 * len(poster), hovermode="closest", xaxis_title=f"Kostnad natten efter ({ENH.strip()})",
          xaxis_range=[lo - 0.18 * (hi - lo), hi + 0.18 * (hi - lo)])
    fig.update_yaxes(autorange="reversed")
    return fig


def fig_storda(R):
    S = dos_serier(R)
    natt = pd.to_datetime(S["datum"]) + pd.Timedelta(days=1)
    fig = go.Figure()
    fig.add_scatter(x=natt, y=S["avv"], mode="markers", marker=dict(size=4, color=MUTED, opacity=.5), name="natt",
                    hovertemplate="%{x|%d %b %Y}: %{y:+.1f}" + ENH + "<extra></extra>")
    m = S["sjuk"] == 1
    fig.add_scatter(x=natt[m], y=S["avv"][m], mode="markers", marker=dict(size=7, color=C_SJUK), name="störd natt",
                    hovertemplate="%{x|%d %b %Y}: %{y:+.1f}" + ENH + "<extra>störd natt</extra>")
    fig.add_hline(y=0, line_color=MUTED, line_width=1)
    return style(fig, 300, hovermode="closest", yaxis_title=f"Natten mot egen nivå ({ENH.strip()})",
                 title="Störda nätter")


@st.cache_data(show_spinner=False)
def stress_karta(v) -> dict:
    return sync.stress_per_timme()


def fig_stresskarta(K):
    dagar = ["mån", "tis", "ons", "tor", "fre", "lör", "sön"]
    z = [[None if (K["antal"][i][h] < 20 or K["medel"][i][h] is None) else K["medel"][i][h] for h in range(24)]
         for i in range(7)]
    timmar = [h for h in range(24) if any(z[i][h] is not None for i in range(7))]
    fig = go.Figure(go.Heatmap(z=[[z[i][h] for h in timmar] for i in range(7)], x=[f"{h:02d}" for h in timmar],
                               y=dagar, colorscale="YlOrRd", colorbar=dict(title="stress"),
                               hovertemplate="%{y} kl %{x}: %{z:.0f}<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    return style(fig, 320, hovermode="closest", xaxis_title="Timme", title="Stress under vaken tid, per veckodag och timme")


# ------------------------------------------------------------------ b) stress och återhämtning
with tab_b:
    if not R.get("ok"):
        st.info(R.get("skal", "Modellen kan inte räknas än."))
    else:
        if VP:
            st.info("**Din klocka mäter inte nattlig HRV**, eller har gjort det för kort tid. Modellen räknas därför "
                    "på vilopulsen i sömnen i stället. Allt nedan är i slag per minut, och **högre vilopuls är sämre**: "
                    "ett pass som kostar syns som ett plus natten efter.")
        if data_source == "oura":
            st.caption("Oura-data: *träningslast* = passets minuter × intensitet (lätt 0,5 / måttlig 1 / hård 2), "
                       "så ett hårt entimmespass är 120 och ett lugnt 30. *Dagsstress* = minuter med hög stress "
                       "enligt Oura. Jämförelser med Garmin-användare gäller därför riktning och storleksordning, "
                       "inte exakta tal.")
        T = None
        if not VP:
            con = dbm.connect()
            T = berattelse.berattelse(bidrag.bygg(R, dbm.participant_id(con), {}, kalla=data_source), grupp=False)
            con.close()
        md = lambda k: berattelse.markdown(T[k]) if T and k in T else ""
        st.markdown(
            f"Modellen tar varje natt och frågar hur mycket av nattens {UTF} som förklaras av det som hände dagen före: "
            "träningen, dagens stress, när passet startade, om du var sjuk, och din egen nivå veckan innan. Allt det "
            "räknas ihop rakt, utan krångliga samband. Det som blir över när de sakerna är borträknade är din form. "
            f"Modellen förutsäger {fmt(R['r2']['form'] * 100, 0)} % av hur dina nätter varierar, och prövas hela tiden "
            "på nätter den inte har sett. Ett typiskt fel är " + fmt(R["mae"]["form"], 1, unit=ENH) + " per natt.")

        st.markdown("#### Vad dagen kostar natten")
        st.plotly_chart(fig_dosrespons(R), width="stretch")
        figurtext(
            "Varje punkt är medelvärdet för nätter efter dagar med den lasten, linjen är vad modellen räknar med. "
            + ("Ligger punkterna över linjen vid hög last kostar hårda pass mer än linjärt hos dig. " if VP else
               "Ligger punkterna under linjen vid hög last kostar hårda pass mer än linjärt hos dig. ")
            + "Mörkblått är natten efter, lila natten därpå. Till höger samma sak för dagsstressen, i fem lika stora "
              "grupper från dina lugnaste till dina mest stressiga dagar.",
            md("last"), md("natt2"))

        st.markdown("#### Sammanfattning i" + ENH)
        poster = kostnadsposter(R)
        st.plotly_chart(fig_kostnader(poster), width="stretch")
        figurtext(*[p[3] for p in poster])
        figurtext(md("timme"))

        if mf_all is not None:
            st.markdown("#### Din form")
            st.plotly_chart(form_chart(mf_all, 300, title=f"Det som blir över ({ENH.strip()})"), width="stretch")
            figurtext("Det här är det som blir över när vi räknat bort träning, stress och sjukdom, och det är den "
                      "kurvan som säger om du är på väg upp eller ner. "
                      + ("Över noll ligger vilopulsen högre än väntat, alltså sämre." if VP else
                         "Över noll återhämtar du dig bättre än träning och stress förklarar."),
                      md("form"), md("sasong"))
            L = R.get("lang_last")
            if L:
                bn = L["banister"]
                with st.expander("Är formen bara uppbyggd träning? (träningens långa minne)"):
                    vinst = bn["vinst_mot_form"]
                    st.markdown(
                        "Ett vanligt sätt att räkna form (Banister, som Stravas Fitness och Freshness) är två "
                        "glidande medel av träningslasten: ett långt för uppbyggd kondition och ett kort för trötthet. "
                        f"Appen har prövat alla kombinationer. Bästa paret för dig är {bn['fitness_hl']} och "
                        f"{bn['trotthet_hl']} dygns halveringstid. Med formen förutsäger modellen "
                        f"{fmt(L['r2_form'] * 100, 1)} % av nätterna, med träningens långa minne också "
                        f"{fmt(bn['r2_med_form'] * 100, 1)} % ({fmt(vinst * 100, 1, True)} procentenheter).")
                    st.markdown("Hos dig tillför träningens långa minne något utöver formen. En del av dina långsamma "
                                "svängningar följer alltså hur mycket du tränat de senaste veckorna." if vinst > 0.002
                                else "Hos dig tillför träningens långa minne inget utöver formen. Formen är alltså "
                                     "inte uppbyggd träning i förklädnad, utan ett tillstånd som träningsmängden inte "
                                     "förklarar.")

        st.markdown("#### Störda nätter")
        st.plotly_chart(fig_storda(R), width="stretch")
        n_st = int(dos_serier(R)["sjuk"].sum())
        figurtext(
            f"Röda punkter är nätter då {'andningen' if VP else 'vilopulsen och andningen'} låg över din egen nivå "
            f"flera dygn i rad, oftast en förkylning eller en period med dålig sömn. Det gäller {n_st} av "
            f"{R['n']} nätter. De räknas bort i figurerna ovan så att de inte ser ut som träningens kostnad.",
            md("sjuk"))

        st.markdown("#### När på dygnet stressen ligger")
        K = stress_karta(v)
        if K["dygn"] < 14:
            st.info("Värmekartan behöver klockans stressvärden var tredje minut, som appen hämtar från version 0.3.4 "
                    "(Garmin). Garmin sparar dem ungefär fyra och en halv månad bakåt, så tryck på Hämta nya data. "
                    + ("Oura har inga sådana värden." if data_source == "oura" else ""))
        else:
            st.plotly_chart(fig_stresskarta(K), width="stretch")
            figurtext(f"Klockans stress var tredje minut under vaken tid, utan sömn och pass, i medel över "
                      f"{K['dygn']} dygn. Det är den mest direkta bilden av livet omkring träningen: morgnar, "
                      "möten, kvällar och helger. Tomma rutor har för få mätningar.")

        with st.expander("Alla modellens tal"):
            e = ENH
            src_t, src_d = R["koef_total"], R["koef"]
            niva_k = "r7" if VP else "h7"
            termer = [("Träningslast (total)", "per 100 i last, natten efter", src_t.get("load"), 100),
                      ("Träningslast (direkt)", "per 100, utöver stressavtrycket", src_d.get("load"), 100),
                      ("Dagsstress", "per enhet samma dag", src_d.get("stress"), 1),
                      ("Gårdagens stress", "per enhet, dagen före", src_d.get("pstress"), 1),
                      ("Gårdagens last", "per 100, dagen före", src_d.get("pload"), 100),
                      ("Styrka", "per 10 minuter", src_d.get("smin"), 10),
                      ("Sen träning", "per timme senare start", src_d.get("hour"), 1),
                      ("Störd natt", "sjukflaggan", src_d.get("sjuk"), 1),
                      ("Egen nivå veckan innan", "per enhet i 7-dygnsnivån", src_d.get(niva_k), 1),
                      ("Form", "per enhet i formen", src_d.get("form"), 1)]
            st.dataframe(pd.DataFrame([{
                "Term": n, "Vad": b, "Effekt": fmt(k["b"] * sk, 2, True, e) if k else "–",
                "t": fmt(k["b"] / k["se"], 1, True) if k and k["se"] else "–",
                "Säker?": "ja" if k and k["se"] and abs(k["b"] / k["se"]) >= 2 else "nej"} for n, b, k, sk in termer]),
                hide_index=True, width="stretch")
            nv = R["nedvarvning"]
            if nv:
                st.markdown(f"Nedvarvning: dagen efter ett pass med last 100 ligger din dagsstress "
                            f"{fmt(nv['b_igar'] * 100, 2, True)} enheter mot ditt 28-dygnssnitt. Negativt betyder att "
                            "kroppen går tystare dagen efter ett hårt pass.")
            st.markdown(f"Ingen framtid läcker in: varje natt från dygn {R['t0']} förutsägs med en modell som bara sett "
                        f"tidigare dygn. Formen är ett glidande medel av modellens tidigare missar med halveringstid "
                        f"{R['hl']} dygn.")

# ------------------------------------------------------------------ c) träningslabb
@st.cache_resource(max_entries=1, show_spinner=False)
def labbet(v):
    P = planerare(v)
    return None if P is None else labb.Labb(run_model(v), P, passtyp_data(v))


TIMMAR = list(range(5, 23))

with tab_c:
    L = labbet(v) if R.get("ok") and not VP else None
    if VP:
        st.info("**Träningslabbet är avstängt när modellen räknas på vilopulsen.** Simuleringen är prövad på "
                "nattlig variabilitet, inte på vilopuls, och vilopulsen rör sig bara några slag per minut, så "
                "skillnaden mellan två upplägg skulle mest vara brus. Vad ett pass kostar hos dig ser du under "
                "Stress och återhämtning.")
    elif L is None:
        st.info(R.get("skal", "Träningslabbet behöver modellen, och den kan inte räknas än."))
    else:
        st.markdown("Lägg upp en vecka som upprepas, och se vad den gör med återhämtningen och med fitness de "
                    "kommande veckorna. Förvalt är din vanliga vecka de senaste åtta veckorna. Varje natt räknas "
                    "på de föregående, så tunga dagar i följd drar ner utgångsläget för dagarna efter.")
        typval = ["Vila"] + list(L.typer) + ["Egen last"]
        mall = []
        cols = st.columns(7)
        for w, col in enumerate(cols):
            f = L.mall_vanlig[w]
            col.markdown(f"**{planering.VECKODAGAR[w]}**")
            typ = col.selectbox("Pass", typval, index=typval.index(f["typ"]), key=f"labb_typ{w}",
                                format_func=lambda t: t.split(":")[0], label_visibility="collapsed")
            val = {"typ": typ}
            if typ == "Egen last":
                val["last"] = col.number_input("Last", 0, 1000, int(round(L.medel_last * 2)), step=10,
                                               key=f"labb_last{w}", label_visibility="collapsed")
            if typ != "Vila":
                t0 = int(round(f.get("timme", L.typer.get(typ, {}).get("timme", 17))))
                val["timme"] = col.selectbox("Start", TIMMAR, index=TIMMAR.index(min(max(t0, 5), 22)),
                                             format_func=lambda h: f"start {h:02d}", key=f"labb_tim{w}",
                                             label_visibility="collapsed")
                if typ in L.typer:
                    col.caption(f"{typ.split(': ')[1]}, last ~{L.typer[typ]['last']:.0f}")
            mall.append(val)

        c1, c2, c3 = st.columns(3)
        veckor = c1.slider("Horisont (veckor)", 4, 12, 8, key="labb_veckor")
        nedv = c2.select_slider("Nedvarvning dagen efter pass", list(labb.NEDVARVNING), value="Som du brukar",
                                key="labb_nedv",
                                help="Bättre nedvarvning = dagen efter ett pass är en halv standardavvikelse lugnare "
                                     "än vanligt för dig, sämre = en halv standardavvikelse stressigare.")
        sjukval = ["Ingen"] + [f"vecka {k + 1}" for k in range(veckor)]
        sj = c3.selectbox("Sjukperiod (fyra dagar, ingen träning)", sjukval, key="labb_sjuk")
        sjuk_vecka = None if sj not in sjukval or sj == "Ingen" else sjukval.index(sj) - 1
        st.caption("Stress utanför träningen, vecka för vecka (i dina egna enheter: lugn "
                   f"{L.stressniva['lugn']:.0f}, medel {L.stressniva['medel']:.0f}, tung {L.stressniva['tung']:.0f}, "
                   f"mycket tung {L.stressniva['mycket tung']:.0f})")
        scols = st.columns(veckor)
        stress_v = [sc.selectbox(f"v{k + 1}", labb.STRESSNIVAER, index=1, key=f"labb_st{k}")
                    for k, sc in enumerate(scols)]

        S = L.kor(mall, veckor, stress_v, nedv, sjuk_vecka)
        idag = pd.Timestamp.today().normalize()
        start = idag + pd.Timedelta(days=7 - idag.weekday())          # nästa måndag
        dagar = pd.date_range(start, periods=S["n"])
        natter = dagar + pd.Timedelta(days=1)
        X = S["indata"]
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.78, 0.22], vertical_spacing=0.05,
                            specs=[[{"secondary_y": True}], [{}]])
        fig.add_scatter(x=list(natter) + list(natter[::-1]), y=list(S["hi"]) + list(S["lo"][::-1]), fill="toself",
                        fillcolor="rgba(42,120,214,.15)", line=dict(width=0, shape="spline"), hoverinfo="skip",
                        name="osäkerhet (80 %)", row=1, col=1)
        # Mjuk linje utan punkter: simuleringen ska inte se mer exakt ut än den är
        fig.add_scatter(x=natter, y=S["medel"], mode="lines", line=dict(color=C_HRV, width=2, shape="spline"),
                        name="HRV", row=1, col=1, hovertemplate="%{y:+.1f} ms<extra>HRV</extra>")
        fig.add_scatter(x=natter, y=S["fitness"], mode="lines", line=dict(color=INK2, width=1.5, dash="dot"),
                        name="fitness (42 d)", row=1, col=1, secondary_y=True,
                        hovertemplate="%{y:.0f}<extra>fitness</extra>")
        fig.add_hline(y=0, line_color=MUTED, line_width=1, row=1, col=1)
        fig.add_hline(y=L.p10, line_dash="dash", line_color=C_NEG, row=1, col=1,
                      annotation_text="din p10", annotation_position="bottom right")
        typfarg = {t: SERIES[i % len(SERIES)] for i, t in enumerate(list(L.typer) + ["Egen last"])}
        typer_dag = [("Sjuk" if X["sjuk"][t] else mall[t % 7]["typ"]) for t in range(S["n"])]
        fig.add_bar(x=dagar, y=X["load"], marker_color=[C_SJUK if t == "Sjuk" else typfarg.get(t, MUTED)
                                                         for t in typer_dag],
                    customdata=typer_dag, showlegend=False, row=2, col=1,
                    hovertemplate="%{customdata}, last %{y:.0f}<extra>passet</extra>")
        if sjuk_vecka is not None:
            s0 = dagar[7 * sjuk_vecka]
            fig.add_vrect(x0=s0 - pd.Timedelta(hours=12), x1=s0 + pd.Timedelta(days=3, hours=12),
                          fillcolor="rgba(227,73,72,.08)", line_width=0, row=1, col=1)
        lo = min(L.p10, S["lo"].min()) - 2
        hi = max(0.0, S["hi"].max()) + 2
        style(fig, 480, hovermode="x unified", title="HRV och fitness med planen")
        fig.update_yaxes(title_text="HRV mot din nivå (ms)", range=[lo, hi], row=1, col=1, secondary_y=False)
        fig.update_yaxes(title_text="Fitness", showgrid=False, row=1, col=1, secondary_y=True,
                         range=[0, 1.4 * max(S["fitness"].max(), L.fitness_nu, 1.0)])
        fig.update_yaxes(title_text="Last", row=2, col=1)
        st.plotly_chart(fig, width="stretch")
        figurtext(
            "Den blå linjen är din HRV natt för natt mot din vanliga nivå, med ett band för hur säker "
            "modellen är på träningens och stressens kostnad. Den streckade röda linjen är din p10, gränsen för "
            "dina tio procent sämsta nätter senaste året. Den prickade linjen är fitness, ett 42-dygns glidande "
            "medel av lasten som Stravas Fitness. Den visar vad träningen bygger, men modellen påstår inte att "
            "den flyttar återhämtningen.")
        st.markdown("**" + L.text(S) + "**")
        lk = planerare(v).lastkostnad
        if planerare(v).sparr:
            st.warning(
                "**Träningens kostnad går inte att skilja från noll i din data**, varken natten efter "
                f"(t {fmt(lk['natt1']['t'], 1, True)}), natten därpå (t {fmt(lk['natt2']['t'], 1, True)}) eller "
                f"tillsammans (t {fmt(lk['summa']['t'], 1, True)}). Kurvan visar modellens bästa gissning, men "
                "olika upplägg skiljer sig mest med brus. Det din återhämtning reagerar på är stressen: pröva "
                "stressveckorna i stället.")
        elif abs(lk["summa"]["b"]) * 100 < 1.0:
            st.info(f"Din träning kostar knappt något ({fmt(lk['summa']['b'] * 100, 1, True, ' ms')} per 100 i last, "
                    "två nätter tillsammans). Spaken för dig är stressen, inte mer eller mindre träning: pröva "
                    "stressveckorna.")
        with st.expander("Så räknar labbet"):
            st.markdown(f"""
Varje natt räknas med din modells koefficienter: dagens och gårdagens last, dagens och gårdagens stress,
passets starttid, styrkeminuter, sjukdom och din nivå veckan innan. Nivån veckan innan är medlet av de sju
**simulerade** nätterna före, så kostnaden av en tung dag följer med in i de närmaste dagarna
(hos dig väger veckan innan {fmt(L.b[L.i[L.niva]], 2)}). Den långsamma formen hålls utanför, den går inte att planera.

Stressen du väljer är stressen **utanför** träningen. Ovanpå den lägger labbet det träningen själv gör med
din dagsstress: {fmt(L.a1 * 100, 1, True)} enheter samma dag och {fmt(L.a2 * 100, 1, True)} dagen efter, per 100 i last.

Passtyperna är dina egna (Träningsöversikt), med medianlast och vanligaste starttid. Bandet är ett
80 %-intervall ur {labb.N_DRAG} dragningar av koefficienterna. Fitness startar på din fitness i dag
({fmt(L.fitness_nu, 0)}), räknad på de senaste 90 dygnen.
""")

# ------------------------------------------------------------------ d) bidrag
with tab_d:
    st.markdown("#### Hjälp till att pröva modellen")
    st.markdown("""
Modellen i appen är byggd på **en** persons fyra år av Garmin-data. Frågan är om den gäller för fler:
kostar ett pass lika mycket för alla, väger stressen lika tungt, och finns den långsamma formen hos andra?
Det går bara att svara på med fler personers resultat.

Du kan bidra med en liten fil med **bara sammanfattade resultat från din modell**. Den innehåller
inga dygnsvärden, inga datum utom startmånaden, inga pass, inga platser och inget namn. Ett slumpat id
skiljer dina bidrag från andras. Du ser hela filen nedan innan du sparar den, och det är du som skickar den.

Sist i filen finns summor över alla dina dygn (hur många dygn, summor och produktsummor av modellens
variabler). Med dem går det att räkna en gemensam modell för alla som bidragit utan att någons
dygnsvärden behöver lämna datorn.
""")
    if not R.get("ok"):
        st.info("Bidraget kan skapas när modellen har kunnat räknas. " + R.get("skal", ""))
    elif is_demo:
        st.info("Du tittar på exempeldata. Bidraget kan skapas när du har hämtat dina egna data.")
    else:
        if VP:
            st.caption("Ditt bidrag märks som en vilopulsmodell och jämförs bara med andra vilopulsbidrag.")
        if R["n"] < 400:
            st.warning(f"Modellen bygger på {R['n']} dygn. Resultaten blir osäkra under ungefär 400 dygn, men "
                       "bidraget är ändå användbart, osäkerheten följer med i filen.")
        with st.form("bakgrund"):
            st.markdown("##### Bakgrund (frivilligt)")
            c1, c2, c3 = st.columns(3)
            alder = c1.selectbox("Åldersgrupp", ["Vill inte ange", "under 30", "30–39", "40–49", "50–59", "60+"])
            kon = c2.selectbox("Kön", ["Vill inte ange", "kvinna", "man", "annat"])
            niva_t = c3.selectbox("Träning", ["Vill inte ange", "motionär", "tävlar ibland", "tävlar regelbundet"])
            klocka = st.text_input("Klockmodell (t.ex. Forerunner 965)")
            kommentar = st.text_input("Något annat som kan vara bra att veta (t.ex. skiftarbete, småbarn)")
            st.form_submit_button("Uppdatera filen")
        con = dbm.connect()
        pid = dbm.participant_id(con)
        con.close()
        b = bidrag.bygg(R, pid, {"alder": alder, "kon": kon, "traning": niva_t, "klocka": klocka.strip(),
                                 "kommentar": kommentar.strip()}, kalla=data_source)
        txt = bidrag.text(b)
        with st.expander(f"Visa hela filen ({len(txt) // 1024 + 1} kB)"):
            st.code(txt, language="json")
        c1, c2 = st.columns([1, 2], vertical_alignment="center")
        c1.download_button("Ladda ner bidragsfilen", txt.encode("utf-8"), file_name=bidrag.filnamn(b),
                           mime="application/json", type="primary")
        if c2.button("Spara i datamappen i stället"):
            p = BIDRAG_DIR / bidrag.filnamn(b)
            p.write_text(txt, encoding="utf-8")
            c2.success(f"Sparad: {p}")
        st.caption(f"Skicka filen till {BIDRAG_MOTTAGARE} som bilaga." if BIDRAG_MOTTAGARE
                   else "Skicka filen som bilaga till den som gav dig appen.")
        st.caption("Ett nytt bidrag om några månader är också värdefullt: då syns om din modell är stabil över tid. "
                   "Samma id gör att det nya ersätter det gamla.")

        # --- din rapport (samma texter som gruppens rapport, utan gruppjämförelsen)
        if not VP:
            st.markdown("#### Din rapport")
            T = berattelse.berattelse(b, grupp=False)
            figurtext(berattelse.markdown(T["last"]))
            kl = [k for k in R["lastklasser"] if k["n"] >= 8]
            if kl:
                lab = ["vila" if k["till"] == 0 else (f"{k['fran']}–{k['till']}" if k["till"] < 1e5 else f">{k['fran']}")
                       for k in kl]
                fig = go.Figure(go.Bar(x=lab, y=[k["avvikelse"] for k in kl], marker_color=C_HRV,
                                       customdata=[k["n"] for k in kl],
                                       hovertemplate="%{y:+.1f} ms · %{customdata} nätter<extra></extra>"))
                fig.add_hline(y=0, line_color=MUTED, line_width=1)
                st.plotly_chart(style(fig, 280, hovermode="closest", xaxis_title="Dagens last",
                                      yaxis_title="Natten mot egen nivå (ms)"), width="stretch")
                figurtext("Återhämtningen natten efter, mot din egen nivå, för dagar med olika mycket last. "
                          "Rakt ur datan, utan modell.")
            figurtext(*[berattelse.markdown(T[k]) for k in ("natt2", "stress", "form", "sasong", "nedvarv", "timme",
                                                            "sjuk", "styrka")])
            st.markdown("##### Råd")
            for r in T["rad"]:
                st.markdown("- " + berattelse.markdown(r))

# ------------------------------------------------------------------ e) data
with tab_e:
    data_page(has_data=True)
    with st.expander("Tabell, dygn för dygn"):
        t = days_all[["datum", "hrv_night", "hrv_7d", "rhr", "sömn_h", "sleep_score", "stress_avg", "skin_temp_dev_c",
                      "steps"] + (["stress_vaken"] if days_all["stress_vaken"].notna().any() else [])].copy()
        dl = acts_all.groupby("date").agg(last=("training_load", "sum"), minuter=("min", "sum"))
        t["date"] = t["datum"].dt.strftime("%Y-%m-%d")
        t = t.merge(dl, left_on="date", right_index=True, how="left").drop(columns="date")
        if mf_all is not None:
            t = t.merge(mf_all[["datum", "form", "pred"]], on="datum", how="left")
        t = t.sort_values("datum", ascending=False)
        t["datum"] = t["datum"].dt.strftime("%Y-%m-%d")
        if VP:
            t = t.drop(columns=["hrv_night", "hrv_7d"])
        t = t.rename(columns={"datum": "Datum", "hrv_night": "HRV natt", "hrv_7d": "HRV 7d", "rhr": "Vilopuls",
                              "sömn_h": "Sömn h", "sleep_score": "Sömnpoäng", "stress_avg": "Stress",
                              "stress_vaken": "Stress vaken", "skin_temp_dev_c": "Hudtemp", "steps": "Steg",
                              "last": "Last", "minuter": "Träning min", "form": "Form",
                              "pred": "Förväntad vilopuls" if VP else "Förväntad HRV"})
        st.caption(("Vilopuls" if VP else "HRV, vilopuls") + " och sömn gäller natten MOT datumet. Last, stress och "
                   "steg gäller dygnet.")
        st.dataframe(t.round(1), hide_index=True, width="stretch", height=500)
