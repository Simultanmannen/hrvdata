"""HRV-data: nattlig HRV, dos–respons och den långsamma formen. Körs med `streamlit run dashboard.py`."""
import json
import sqlite3

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import bidrag
import db as dbm
import jobs
import modell
import oura
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
    update_banner()


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


def form_chart(mf, height=300, title="Långsam form (ms)"):
    fig = go.Figure()
    pos, neg = mf["form"].clip(lower=0), mf["form"].clip(upper=0)
    fig.add_scatter(x=mf["datum"], y=pos, fill="tozeroy", mode="lines", line=dict(width=0, color=C_POS),
                    fillcolor="rgba(27,175,122,.35)", hoverinfo="skip", showlegend=False)
    fig.add_scatter(x=mf["datum"], y=neg, fill="tozeroy", mode="lines", line=dict(width=0, color=C_NEG),
                    fillcolor="rgba(227,73,72,.30)", hoverinfo="skip", showlegend=False)
    fig.add_scatter(x=mf["datum"], y=mf["form"], mode="lines", line=dict(color=INK2, width=1.2),
                    hovertemplate="%{y:+.1f} ms<extra>form</extra>", showlegend=False)
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
    brand("Nattlig HRV och dos–respons")
    st.markdown("## Välkommen")
    st.write("Appen hämtar din nattliga HRV, sömn, stress och dina pass från Garmin Connect eller Oura och sparar dem här "
             "på datorn. Sedan räknar den ut hur din kropp svarar på träning och vardagsstress, och hur mycket "
             "av din HRV som beror på ett långsamt tillstånd som klockan själv inte ser.")
    job_panel()
    data_page(has_data=False)
    st.stop()

acts_all = load_acts(v)
is_demo = (days_all["fetched_at"] == "demo").any()

with st.sidebar:
    st.header("Visa")
    periods = {"30 dagar": 30, "90 dagar": 90, "Ett år": 365, "Allt": None}
    pchoice = st.radio("Period", list(periods), index=1)
    st.divider()
    if jobs.source():
        sync_clicked = st.button("Hämta nya data", width="stretch", type="primary", disabled=jobs.JOB.running)
    else:
        sync_clicked = False
        st.caption("Inte inloggad mot Garmin eller Oura – se fliken Data & konto.")
    st.caption(f"{days_all['hrv_night'].notna().sum()} nätter med HRV · {len(acts_all)} pass · version {VERSION}")
if sync_clicked:
    start_sync(incremental_days(days_all))

last_day = days_all["datum"].max()
d0 = days_all["datum"].min() if periods[pchoice] is None else last_day - pd.Timedelta(days=periods[pchoice] - 1)
days = days_all[days_all["datum"] >= d0]
acts = acts_all[acts_all["datum"] >= d0]

brand(("Exempeldata · " if is_demo else "") + f"{sv(d0, True)} – {sv(last_day, True)}")
job_panel()

with st.spinner("Räknar modellen …"):
    R = run_model(v)
mf_all = model_frame(R) if R.get("ok") else None
mf = mf_all[mf_all["datum"] >= d0] if mf_all is not None else None

tab_over, tab_dos, tab_plan, tab_form, tab_health, tab_train, tab_table, tab_bidra, tab_data = st.tabs(
    ["Översikt", "Dos–respons", "Planera veckan", "Långsam form", "Hälsa", "Träning", "Tabell", "Bidra",
     "Data & konto"])

with tab_data:
    data_page(has_data=True)

_con = dbm.connect()
data_source = dbm.meta_get(_con, "kalla") or jobs.source()
_con.close()

# ------------------------------------------------------------------ översikt
with tab_over:
    hv = days_all.dropna(subset=["hrv_night"])
    last = hv.iloc[-1] if not hv.empty else None
    c = st.columns(6)
    if last is not None:
        c[0].metric(f"HRV senaste natten ({sv(last['datum'])})", fmt(last["hrv_night"], 0, unit=" ms"),
                    None if pd.isna(last["hrv_7d"]) else delta(last["hrv_night"] - last["hrv_7d"], 0, " ms mot 7 dygn"))
        c[1].metric("HRV 7-dygnssnitt", fmt(last["hrv_7d"], 0, unit=" ms"))
    lr = days_all.dropna(subset=["rhr"])
    if not lr.empty:
        c[2].metric("Vilopuls", fmt(lr.iloc[-1]["rhr"], 0, unit=" slag/min"))
    ls = days_all.dropna(subset=["sleep_seconds"])
    if not ls.empty:
        c[3].metric("Sömn", fmt(ls.iloc[-1]["sömn_h"], 1, unit=" h"))
    lx = days_all.dropna(subset=["stress_avg"])
    if not lx.empty:
        lxr = lx.iloc[-1]
        partial = lxr["fetched_at"] != "demo" and not sync.complete(lxr["date"], lxr["fetched_at"])
        c[4].metric(f"Stress ({sv(lxr['datum'])}{', hittills' if partial else ''})", fmt(lxr["stress_avg"], 0),
                    help="Garmins dagssnitt. Ett dygn som hämtades innan det var slut är ofullständigt och "
                         "fylls på vid nästa hämtning." if partial else None)
    if mf_all is not None:
        c[5].metric("Långsam form", fmt(mf_all["form"].iloc[-1], 1, True, " ms"),
                    help="Ett tillstånd som modellen ser i hur dina nätter avviker från det väntade de senaste "
                         "dagarna. Positivt = kroppen ligger bättre till än vad träning och stress förklarar.")

    if mf_all is not None and last is not None:
        lastrow = mf_all.iloc[-1]
        diff = lastrow["hrv"] - lastrow["pred"]
        if np.isfinite(diff):
            word = "över" if diff > 0 else "under"
            st.caption(f"Natten mot {sv(lastrow['datum'])} landade {fmt(abs(diff), 0)} ms {word} vad modellen "
                       f"väntade sig ({fmt(lastrow['pred'], 0)} ms) utifrån gårdagens träning, stress och form. "
                       f"Modellens typiska fel är {fmt(R['mae']['form'], 1)} ms.")
    st.plotly_chart(hrv_chart(days, mf), width="stretch")
    if mf is not None and not mf.empty:
        st.plotly_chart(form_chart(mf, 240), width="stretch")
    elif not R.get("ok"):
        st.info(R.get("skal", "Modellen kan inte räknas än."))

# ------------------------------------------------------------------ dos–respons
REF = referens()


def koefrad(namn, beskr, key, skala, enhet, total=False):
    src = R["koef_total"] if total else R["koef"]
    k = src.get(key)
    rk = (REF or {}).get("koef_total" if total else "koef_direkt", {}).get(key) if REF else None
    t = k["b"] / k["se"] if k and k["se"] else None
    return {"Term": namn, "Vad den mäter": beskr,
            "Din effekt": fmt(k["b"] * skala, 2, True, enhet) if k else "–",
            "t-värde": fmt(t, 1, True) if t is not None else "–",
            "Säker?": "ja" if t is not None and abs(t) >= 2 else "nej",
            "Ursprungsserien": fmt(rk["b"] * skala, 2, True, enhet) if rk else "–"}


with tab_dos:
    if not R.get("ok"):
        st.info(R.get("skal", "Modellen kan inte räknas än."))
    else:
        if data_source == "oura":
            st.caption("Oura-data: *träningslast* = passets minuter × intensitet (lätt 0,5 / måttlig 1 / hård 2), "
                       "så ett hårt entimmespass är 120 och ett lugnt 30. *Dagsstress* = minuter med hög stress "
                       "enligt Oura (finns från hösten 2023 och bara dagar du burit ringen). Jämförelser med "
                       "Garmin-användare gäller därför riktning och storleksordning, inte exakta tal.")
        st.markdown("#### Vad kostar det i natt?")
        kt, kd = R["koef_total"]["load"], R["koef"]["load"]
        tp = R["tungt_pass"]
        c = st.columns(4)
        c[0].metric("Pass med träningslast 100", fmt(kt["b"] * 100, 1, True, " ms"),
                    help="Hur mycket lägre HRV natten efter ett pass med Garmins träningslast 100, allt annat lika. "
                         "Total effekt: inklusive den del som går via att passet höjer dagens stressvärde.")
        c[1].metric("…varav utöver stressavtrycket", fmt(kd["b"] * 100, 1, True, " ms"),
                    help="Samma sak, men när dagens stress hålls konstant. Skillnaden mot den totala effekten är "
                         "den del av passets kostnad som syns som höjd stress under dagen.")
        c[2].metric("En stressenhet över ditt vanliga", fmt(R["koef"]["stress"]["b"], 2, True, " ms"),
                    help="Garmins dagsstress. 5 enheter över en normal dag är ungefär 5 gånger så mycket.")
        if tp:
            n2 = "" if tp["natt2"] is None else f" Natten därpå: {fmt(tp['natt2'], 1, True, ' ms')}."
            c[3].metric(f"Tungt pass (last ≥ {tp['grans_last']:.0f})", fmt(tp["natt1"], 1, True, " ms"),
                        help=f"Modellfritt: dina {tp['n']} tyngsta passdygn (översta tiondelen) mot vilodygn. "
                             f"HRV natten efter jämfört med 7-dygnssnittet.{n2}")
            if n2:
                c[3].caption(n2.strip())

        left, right = st.columns([1.1, 1], gap="large")
        with left:
            kl = pd.DataFrame(R["lastklasser"])
            if not kl.empty:
                kl["klass"] = [("Vila" if r.till == 0 else (f"{r.fran}–{r.till}" if r.till < 10 ** 5 else f"{r.fran}+"))
                               for r in kl.itertuples()]
                fig = go.Figure(go.Bar(x=kl["klass"], y=kl["avvikelse"], marker_color=C_HRV,
                                       customdata=kl["n"],
                                       hovertemplate="%{y:+.1f} ms<br>%{customdata} dygn<extra></extra>"))
                fig.add_hline(y=0, line_color=MUTED, line_width=1)
                style(fig, 320, title="HRV natten efter mot 7-dygnssnittet, per träningslast (ms)",
                      xaxis_title="Dagens träningslast", hovermode="closest")
                st.plotly_chart(fig, width="stretch")
                st.caption("Rakt ur datan, utan modell. Sjukdygn är borttagna.")
        with right:
            sc = R["scan"]
            fig = go.Figure(go.Scatter(x=list(sc), y=list(sc.values()), mode="lines+markers",
                                       line=dict(color=C_7D), hovertemplate="%{x} dygn: R² %{y:.3f}<extra></extra>"))
            fig.add_hline(y=R["r2"]["bas"], line_dash="dot", line_color=MUTED,
                          annotation_text="utan form", annotation_position="bottom right")
            style(fig, 320, title="Hur mycket formen tillför, per halveringstid", xaxis_title="Halveringstid (dygn)",
                  yaxis_title="R² ut ur stickprovet", hovermode="closest")
            fig.update_xaxes(type="log", tickvals=list(sc))
            st.plotly_chart(fig, width="stretch")

        st.markdown("#### Modellen")
        c = st.columns(4)
        c[0].metric("Förklarad variation, utan form", fmt(R["r2"]["bas"] * 100, 0, unit=" %"),
                    help="R² ut ur stickprovet: varje natt förutsägs med en modell som bara sett tidigare nätter.")
        c[1].metric("…med den långsamma formen", fmt(R["r2"]["form"] * 100, 0, unit=" %"),
                    delta((R["r2"]["form"] - R["r2"]["bas"]) * 100, 1, " procentenheter"))
        c[2].metric("Typiskt fel", fmt(R["mae"]["form"], 1, unit=" ms"))
        c[3].metric("Dygn i modellen", f"{R['n']}",
                    help=f"Förklarad variation och typiskt fel är prognoser för de sista {R['n_utv']} dygnen.")
        rows = [
            koefrad("Träningslast (total)", "per 100 enheter, natten efter", "load", 100, " ms", total=True),
            koefrad("Träningslast (direkt)", "per 100 enheter, utöver stressavtrycket", "load", 100, " ms"),
            koefrad("Dagsstress", "per enhet Garmin-stress samma dag", "stress", 1, " ms"),
            koefrad("Gårdagens stress", "per enhet, dagen före", "pstress", 1, " ms"),
            koefrad("Gårdagens last", "per 100 enheter, dagen före", "pload", 100, " ms"),
            koefrad("Styrka", "per 10 minuter styrketräning", "smin", 10, " ms"),
            koefrad("Sen träning", "per timme senare start på dagens sista pass", "hour", 1, " ms"),
            koefrad("Sjuk", "dygn då vilopuls och andning är förhöjda", "sjuk", 1, " ms"),
            koefrad("7-dygnssnitt", "per ms i Garmins 7-dygnssnitt", "h7", 1, " ms"),
            koefrad("Långsam form", "per ms i formen", "form", 1, " ms"),
        ]
        tabell = pd.DataFrame(rows)
        if REF is None:  # referens.json följer inte med i publika paket
            tabell = tabell.drop(columns="Ursprungsserien")
        st.dataframe(tabell, hide_index=True, width="stretch")
        if REF is not None:
            st.caption("Ursprungsserien är de fyra år av data som modellen ursprungligen byggdes på. Jämförelsen "
                       "visar om du svarar likadant – det är precis det bidragen ska pröva.")
        nv = R["nedvarvning"]
        if nv:
            st.caption(f"Nedvarvning: dagen efter ett pass med last 100 ligger din dagsstress "
                       f"{fmt(nv['b_igar'] * 100, 2, True)} enheter mot ditt 28-dygnssnitt "
                       f"(t {fmt(nv['b_igar'] / nv['se_igar'], 1, True)}). Negativt betyder att kroppen går "
                       "tystare dagen efter ett hårt pass.")
        with st.expander("Hur modellen räknar"):
            st.markdown(f"""
HRV natten efter dygn *d* förklaras av det som är känt före natten: Garmins 7-dygnssnitt, dagens och
gårdagens stress, dagens och gårdagens träningslast, minuter styrketräning, när på dagen sista passet
startade och om dygnet ser ut som ett sjukdygn (vilopuls och andning över din egen 60-dygnsmedian).
Allt är linjärt. Det är medvetet: flexiblare modeller förutsäger sämre på den här sortens data.

**Ingen framtid läcker in.** Varje natt från dygn {R['t0']} och framåt förutsägs med en modell som bara
har sett tidigare dygn (expanderande fönster). Siffrorna för förklarad variation är alltså ärliga
prognoser, inte efterhandsanpassning.

**Den långsamma formen** är ett glidande medel av modellens tidigare missar, med den halveringstid som
förutsäger bäst ({R['hl']} dygn för dig). Koefficienterna i tabellen är skattade på alla dygn.
""")

# ------------------------------------------------------------------ planera veckan
STRESSFARG = {"lugn": C_POS, "normal": MUTED, "tung": C_NEG, "mycket tung": "#9b1c1b"}

with tab_plan:
    P = planerare(v) if R.get("ok") else None
    if P is None:
        st.info(R.get("skal", "Planeraren behöver modellen, och den kan inte räknas än."))
    else:
        st.markdown("Målet är en **jämn träningsnivå** vecka efter vecka, med HRV kvar i ditt normala spann. "
                    "Ange hur stressig varje dag ser ut att bli, utan träningen. Planeraren lägger din vanliga "
                    "veckas pass så att så få nätter som möjligt hamnar under spannet.")
        nu = P.nulage()
        imorgon = (pd.Timestamp.today().normalize() + pd.Timedelta(days=1)).date()
        start = st.date_input("Veckan börjar", imorgon, format="YYYY-MM-DD", key="plan_start")
        if nu["medel"] is not None:
            nn = ", ".join(f"{v:.0f}" for _, v in nu["natter"])
            bana = P.stressbana(start, ["normal"] * 7)[0]
            arv0, arv3 = bana[0] - P.niva["normal"], bana[3] - P.niva["normal"]
            txt = (f"**Nuläge.** Dina senaste nätter: {nn} ms (snitt 60 nätter {fmt(P.bas, 0)}). Övrig stress de "
                   f"senaste {len(nu['dagar'])} fullständiga dygnen: snitt {fmt(nu['medel'], 0)} "
                   f"(**{nu['niva']}** för dig, normalt {fmt(P.niva['normal'], 0)}).")
            if abs(arv0) >= 0.5:
                txt += (f" Stress hänger kvar: en dag du bedömer som normal väntas därför mätas "
                        f"{fmt(arv0, 1, True)} över det vanliga första dagen och {fmt(arv3, 1, True)} fjärde dagen. "
                        "Det lägger modellen på själv – bedöm varje dag för sig.")
            st.info(txt)
        st.caption("Per dag: övre rutan är dagens övriga stress (utan träningen), undre rutan låser dagen.")
        cols = st.columns(7)
        nivaer, las = [], []
        niv_lista = list(planering.NIVAER)
        for i, col in enumerate(cols):
            d = pd.Timestamp(start) + pd.Timedelta(days=i)
            col.markdown(f"**{planering.VECKODAGAR[d.weekday()]}** {d.day}/{d.month}")
            # Etiketterna dolda i alla kolumner, annars blir första kolumnen högre än de andra
            nivaer.append(col.selectbox("Övrig stress", niv_lista, index=niv_lista.index("normal"),
                                        key=f"plan_s{i}", label_visibility="collapsed"))
            las.append(col.selectbox("Låst", planering.LAS, index=0, key=f"plan_l{i}",
                                     label_visibility="collapsed"))
        vv = P.vanlig
        with st.expander(f"Veckans träning – förvalt din vanliga vecka: {vv['last']:.0f} i last, "
                         f"{vv['dagar']} passdagar, {vv['hard']} hårda, {vv['styrka']} styrkepass"):
            c1, c2, c3, c4, c5 = st.columns(5)
            veckolast = c1.number_input("Veckolast (uthållighet)", 0, 3000, int(round(vv["last"])), step=10,
                                        key="plan_last", help="Garmins träningslast summerad över veckan, utan "
                                        "styrkepassen. Förvalt: median av dina senaste åtta veckor.")
            pdagar = c2.number_input("Passdagar", 0, 7, vv["dagar"], key="plan_dagar")
            phard = c3.number_input(f"Varav hårda (≥ {P.hard_grans:.0f})", 0, 7, min(vv["hard"], pdagar),
                                    key="plan_hard", help=f"Ett hårt pass räknas som ditt typiska hårda pass, "
                                    f"last ~{P.hard_last:.0f}.")
            pstyrka = c4.number_input("Styrkepass", 0, 7, vv["styrka"], key="plan_styrka")
            vila_val = {"1": 1, "2": 2, "3": 3, "Ingen gräns": None}
            mvila = vila_val[c5.selectbox("Max vilodagar i rad", list(vila_val), index=1, key="plan_vila",
                                          help="Håller nivån jämn. Modellen ser bara kostnader, inte nyttan av "
                                               "regelbundenhet, så utan gräns samlar den gärna passen.")]
            sdagar = st.multiselect("Styrka på bestämda dagar (valfritt)", list(range(7)), key="plan_sdagar",
                                    format_func=lambda i: planering.VECKODAGAR[(pd.Timestamp(start) +
                                                                               pd.Timedelta(days=i)).weekday()])
            st.caption("Veckorna bakom förvalet (senaste först): " + " · ".join(
                f"{w['last']:.0f}/{w['dagar']}d" for w in vv["veckor"]))
        F = P.foreslag(start, nivaer, veckolast=float(veckolast), dagar=int(pdagar), hard=int(phard),
                       styrka=len(sdagar) if sdagar else int(pstyrka), las=las, styrkedagar=sdagar or None,
                       max_vila_i_rad=mvila)
        if not F["ok"]:
            st.warning(F["skal"])
        else:
            pl = F["plan"]
            c = st.columns(4)
            c[0].metric("Nätter under spannet, förslaget", fmt(pl["under"], 1),
                        delta(pl["under"] - F["vanlig_under"], 1, " mot ditt vanliga mönster"),
                        delta_color="inverse",
                        help="Förväntat antal av veckans sju nätter under ditt normalspann, med modellens "
                             "osäkerhet inräknad.")
            c[1].metric("…med ditt vanliga veckomönster", fmt(F["vanlig_under"], 1),
                        help="Samma veckolast fördelad som du brukar fördela den över veckodagarna.")
            c[2].metric("…utan någon träning", fmt(F["utan_traning_under"], 1),
                        help="Vad veckans stress ensam ger. Skillnaden mot förslaget är träningens kostnad.")
            c[3].metric("Sämsta natten", fmt(pl["samsta"], 0, unit=" ms"),
                        f"sämsta möjliga upplägg: {fmt(F['samsta_upplagg']['samsta'], 0, unit=' ms')}",
                        delta_color="off")
            dd = pd.DataFrame(pl["dagar"])
            labels = [f"{r.veckodag} {int(r.datum[8:])}/{int(r.datum[5:7])}" for r in dd.itertuples()]
            fig = go.Figure()
            band = 0.674 * F["sigma"]
            fig.add_bar(x=labels, y=dd["hrv"], name="Förslaget",
                        marker_color=[STRESSFARG[s] for s in dd["stress"]],
                        error_y=dict(type="constant", value=band, color=INK2, thickness=1),
                        customdata=np.c_[dd["p_under"] * 100, dd["roll"]],
                        hovertemplate="%{y:.0f} ms · %{customdata[1]} · risk under spannet %{customdata[0]:.0f} %"
                                      "<extra></extra>")
            fig.add_scatter(x=labels, y=F["vanlig_hrv"], mode="markers", name="Ditt vanliga mönster",
                            marker=dict(symbol="line-ew-open", size=26, color=INK, line=dict(width=2)),
                            hovertemplate="%{y:.0f} ms<extra>vanligt mönster</extra>")
            fig.add_hline(y=F["grans"], line_dash="dash", line_color=C_NEG,
                          annotation_text="normalspannets undre gräns", annotation_position="bottom right")
            fig.add_hline(y=F["bas"], line_color=MUTED, line_width=1,
                          annotation_text="ditt snitt, 60 nätter", annotation_position="top right")
            lo = min(F["grans"], dd["hrv"].min()) - band - 5
            style(fig, 340, title="Förväntad HRV natten efter varje dag (ms)", hovermode="closest",
                  yaxis_range=[lo, max(F["bas"], dd["hrv"].max()) + band + 5])
            st.plotly_chart(fig, width="stretch")
            st.caption("Staplarnas färg visar dagens övriga stress (grön lugn, grå normal, röd tung). "
                       "Strecket på stapeln är ett 50 %-intervall: hälften av nätterna brukar hamna inom det.")
            tbl = pd.DataFrame({
                "Dag": labels, "Övrig stress": dd["stress"],
                "Väntad stress": dd["stress_vantad"].round(0).astype(int), "Träning": dd["roll"],
                "Förslag": dd["forslag"], "Last": dd["last"].round(0).astype(int),
                "HRV natten efter": dd["hrv"].round(0).astype(int),
                "Risk under spannet": (dd["p_under"] * 100).round(0).astype(int).astype(str) + " %"})
            st.dataframe(tbl, hide_index=True, width="stretch")
        with st.expander("Så räknar planeraren, och vad den inte kan"):
            st.markdown(f"""
**Övrig stress** är Garmins dagsstress rensad från det påslag som träningen själv ger. För dig motsvarar
*lugn* ungefär {fmt(P.niva['lugn'], 0)}, *normal* {fmt(P.niva['normal'], 0)}, *tung*
{fmt(P.niva['tung'], 0)} och *mycket tung* {fmt(P.niva['mycket tung'], 0)} i Garmins stressvärde: dina
20 % lugnaste, mediandagen, dina 15 % tyngsta och dina 5 % tyngsta dagar det senaste året. Grovt räcker.
Det viktiga är att skatta ärligt, inte exakt.

**Eftersläpningen.** Du bedömer varje dag för sig. Modellen lägger på det som hänger kvar från dagarna
före, på två sätt:
- *Stressen är trög.* Dagens stress ärver {fmt(P.tr1, 2)} av gårdagens överskott och {fmt(P.tr2, 2)} av
  veckans. Efter en stressig period mäts en dag du upplever som lugn sällan som lugn – kroppen är
  fortfarande uppvarvad. Kolumnen *Väntad stress* visar vad modellen räknar med.
- *HRV minns stressen.* Utöver dagens och gårdagens stress drar ett 7-dygns stressminne ner nätterna
  ({fmt(P.b['minne'], 2, True)} ms per enhet över din median).

Däremot förs nattens nivå inte rakt fram. Prövat på ursprungsseriens fyra år: när en dag faktiskt *mäts* som lugn
studsar HRV tillbaka även efter flera stressiga dygn (natten efter 77,7 ms, lika mycket som efter lugna
perioder), och hur mycket de senaste nätterna låg under det väntade säger inget om veckan efter utöver
formen (korrelation högst 0,09). Eftersläpningen sitter i stressen, inte i HRV:n.

**Varför placeringen spelar roll.** Kostnaderna adderas: en tung dag och ett pass samma dag ger en natt
där båda dras av. Veckans *medel* blir detsamma hur passen än läggs, men antalet nätter under spannet
gör det inte. Planeraren prövar alla sätt att lägga veckans pass ({F.get('n_kandidater', 0)} upplägg)
och väljer det med lägst förväntat antal nätter under spannet.

**Normalspannet** är snittet av dina senaste 60 nätter ({fmt(P.bas, 0)} ms) minus en halv
standardavvikelse ({fmt(P.grans, 0)} ms). Modellens osäkerhet per natt är ±{fmt(P.sigma, 0)} ms
(standardavvikelse). Sannolikheterna stämmer på ursprungsseriens fyra år: i snitt förutsades 29 % av nätterna hamna
under spannet, och 30 % gjorde det.

**Vad den inte kan:** den vet vad träning *kostar*, inte vad den *ger*. Intervaller, zoner, progression
och vilka pass som gör dig bättre får du bestämma själv. Den rör inte veckans volym – målet är en jämn
nivå. Och passförslagen i minuter bygger på hur mycket last per minut dina egna pass brukar ge.
""")

# ------------------------------------------------------------------ långsam form
with tab_form:
    if mf_all is None:
        st.info(R.get("skal", "Modellen kan inte räknas än."))
    else:
        st.markdown("Modellens missar är inte slumpmässiga. De hänger ihop i dagar och veckor, som om kroppen "
                    "har ett tillstånd som varken träningslast, stress eller sömn fångar. Formen är det "
                    "tillståndet, uppskattat ur hur dina senaste nätter avvikit från det väntade.")
        c = st.columns(4)
        c[0].metric("Just nu", fmt(mf_all["form"].iloc[-1], 1, True, " ms"))
        c[1].metric("Spridning (sd)", fmt(R["form_sd"], 1, unit=" ms"))
        a1 = R["form_acf"].get(1)
        mem = np.log(0.5) / np.log(a1) if a1 and 0 < a1 < 1 else None
        c[2].metric("Formens minne", fmt(mem, 0, unit=" dygn") if mem else "–",
                    help="Halveringstid räknad ur formens autokorrelation från en dag till nästa.")
        c[3].metric("Bästa filter", f"{R['hl']} dygn",
                    help="Halveringstiden i det glidande medel av missar som förutsäger bäst.")
        st.plotly_chart(form_chart(mf, 320), width="stretch")
        left, right = st.columns(2, gap="large")
        with left:
            fm = R["form_manad"]
            if fm:
                xs = [MANADER[int(m) - 1] for m in fm]
                ys = list(fm.values())
                fig = go.Figure(go.Bar(x=xs, y=ys, marker_color=[C_POS if y > 0 else C_NEG for y in ys],
                                       hovertemplate="%{y:+.1f} ms<extra></extra>"))
                fig.add_hline(y=0, line_color=MUTED, line_width=1)
                st.plotly_chart(style(fig, 280, title="Formen per månad, snitt (ms)", hovermode="closest"),
                                width="stretch")
        with right:
            acfd = {int(k): v for k, v in R["form_acf"].items() if v is not None}
            fig = go.Figure(go.Bar(x=[f"{k} d" for k in acfd], y=list(acfd.values()), marker_color=C_7D,
                                   hovertemplate="%{y:.2f}<extra></extra>"))
            st.plotly_chart(style(fig, 280, title="Hur länge formen hänger i (autokorrelation)",
                                  hovermode="closest", yaxis_range=[min(0, *acfd.values()) - .05, 1]),
                            width="stretch")

# ------------------------------------------------------------------ hälsa
with tab_health:
    pairs = [("rhr", "Vilopuls (slag/min)", SERIES[7], ""), ("sömn_h", "Sömn (timmar)", SERIES[6], " h"),
             ("stress_avg", "Stress, dagssnitt", SERIES[1], ""), ("skin_temp_dev_c", "Hudtemperatur, avvikelse (°C)",
                                                                  SERIES[3], " °C"),
             ("respiration_avg", "Andning i sömn (andetag/min)", SERIES[2], ""), ("steps", "Steg per dag", SERIES[0], "")]
    pairs = [p for p in pairs if days[p[0]].notna().any()]
    for i in range(0, len(pairs), 2):
        cols = st.columns(2)
        for col, (k, t, colr, u) in zip(cols, pairs[i:i + 2]):
            col.plotly_chart(line_chart(days, k, t, colr, u), width="stretch")

# ------------------------------------------------------------------ träning
with tab_train:
    if acts.empty:
        st.info("Inga pass i vald period.")
    else:
        a = acts.copy()
        a["vecka"] = a["datum"].dt.to_period("W-SUN").dt.start_time
        wk = a.pivot_table(index="vecka", columns="grupp", values="min", aggfunc="sum", fill_value=0)
        fig = go.Figure()
        for g in [g for g in GRUPPNAMN if g in wk.columns]:
            fig.add_bar(x=wk.index, y=wk[g] / 60, name=GRUPPNAMN[g], marker_color=GRUPPFARG[g],
                        hovertemplate="%{y:.1f} h<extra>" + GRUPPNAMN[g] + "</extra>")
        st.plotly_chart(style(fig, 340, barmode="stack", title="Träning per vecka (timmar)"), width="stretch")
        wl = a.groupby("vecka")["training_load"].sum()
        fig = go.Figure(go.Bar(x=wl.index, y=wl, marker_color=SERIES[1], hovertemplate="%{y:.0f}<extra></extra>"))
        st.plotly_chart(style(fig, 260, title="Träningslast per vecka"), width="stretch")
        c = st.columns(4)
        c[0].metric("Pass", len(a))
        c[1].metric("Timmar", fmt(a["min"].sum() / 60, 0))
        c[2].metric("Last per dygn", fmt(a["training_load"].sum() / max(1, len(days)), 0))
        c[3].metric("Andel dygn med pass", fmt(100 * a["date"].nunique() / max(1, len(days)), 0, unit=" %"))
        show = a.sort_values("start_local", ascending=False)[
            ["start_local", "name", "type_key", "min", "training_load", "aerobic_te", "avg_hr"]]
        show.columns = ["Start", "Namn", "Typ", "Minuter", "Last", "Aerob TE", "Medelpuls"]
        st.dataframe(show.round(1), hide_index=True, width="stretch", height=320)

# ------------------------------------------------------------------ tabell
with tab_table:
    t = days[["datum", "hrv_night", "hrv_7d", "rhr", "sömn_h", "sleep_score", "stress_avg",
              "skin_temp_dev_c", "steps"]].copy()
    dl = acts.groupby("date").agg(last=("training_load", "sum"), minuter=("min", "sum"))
    t["date"] = t["datum"].dt.strftime("%Y-%m-%d")
    t = t.merge(dl, left_on="date", right_index=True, how="left").drop(columns="date")
    if mf_all is not None:
        t = t.merge(mf_all[["datum", "form", "pred"]], on="datum", how="left")
    t = t.sort_values("datum", ascending=False)
    t["datum"] = t["datum"].dt.strftime("%Y-%m-%d")
    t = t.rename(columns={"datum": "Datum", "hrv_night": "HRV natt", "hrv_7d": "HRV 7d", "rhr": "Vilopuls",
                          "sömn_h": "Sömn h", "sleep_score": "Sömnpoäng", "stress_avg": "Stress",
                          "skin_temp_dev_c": "Hudtemp", "steps": "Steg", "last": "Last", "minuter": "Träning min",
                          "form": "Form", "pred": "Förväntad HRV"})
    st.caption("HRV, vilopuls och sömn gäller natten MOT datumet. Last, stress och steg gäller dygnet. "
               "Form och förväntad HRV gäller natten mot datumet.")
    st.dataframe(t.round(1), hide_index=True, width="stretch", height=600)

# ------------------------------------------------------------------ bidra
with tab_bidra:
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
        if R["n"] < 400:
            st.warning(f"Modellen bygger på {R['n']} dygn. Resultaten blir osäkra under ungefär 400 dygn, men "
                       "bidraget är ändå användbart – osäkerheten följer med i filen.")
        with st.form("bakgrund"):
            st.markdown("##### Bakgrund (frivilligt)")
            c1, c2, c3 = st.columns(3)
            alder = c1.selectbox("Åldersgrupp", ["Vill inte ange", "under 30", "30–39", "40–49", "50–59", "60+"])
            kon = c2.selectbox("Kön", ["Vill inte ange", "kvinna", "man", "annat"])
            niva = c3.selectbox("Träning", ["Vill inte ange", "motionär", "tävlar ibland", "tävlar regelbundet"])
            klocka = st.text_input("Klockmodell (t.ex. Forerunner 965)")
            kommentar = st.text_input("Något annat som kan vara bra att veta (t.ex. skiftarbete, småbarn)")
            st.form_submit_button("Uppdatera filen")
        con = dbm.connect()
        pid = dbm.participant_id(con)
        con.close()
        b = bidrag.bygg(R, pid, {"alder": alder, "kon": kon, "traning": niva, "klocka": klocka.strip(),
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
