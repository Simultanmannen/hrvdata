# HRV-data

Lokal app som hämtar din nattliga HRV, sömn, stress och dina träningspass från Garmin Connect eller
Oura och räknar ut hur din kropp svarar på träning och vardagsstress. All data stannar på din dator.

**Vill du bara använda appen?** Ladda ner zip-filen under
[Releases](https://github.com/Simultanmannen/hrvdata/releases/latest), packa upp den och läs
`LÄSMIG.txt`. Python behövs inte i förväg.

## Vad appen räknar ut

- **Dos–respons:** hur mycket lägre HRV natten efter ett pass, per enhet träningslast och per enhet
  dagsstress. Modellen är linjär och utvärderas ut ur stickprovet med expanderande fönster, så att
  ingen framtid läcker in i prognoserna.
- **Långsam form:** ett glidande medel av modellens tidigare missar. Fångar perioder då HRV ligger
  över eller under vad träning och stress förklarar.
- **Veckoplanerare:** lägger din vanliga veckas pass så att så få nätter som möjligt hamnar under ditt
  normalspann, givet hur stressig varje dag ser ut att bli. Volymen ändras inte, bara placeringen.
- **Bidrag (frivilligt):** en liten fil med sammanfattade resultat från din modell, utan dygnsvärden
  och utan datum, för att pröva hur allmängiltig modellen är. Du ser hela filen innan du skickar den.

## Integritet

Appen pratar bara med Garmin Connect eller Oura (för att hämta dina data) och med GitHub (för att
leta efter nya versioner). Lösenordet sparas inte, bara en inloggningsnyckel i
mappen `data/` på din dator. Inget skickas någonstans utan att du själv gör det.

## Filer

| fil | vad |
|---|---|
| `Starta HRV-data.bat` / `.command` | Startfiler för Windows och Mac. Laddar ner uv, Python 3.12 och paketen till `.verktyg/` första gången. |
| `starta.py` | Väljer ledig port, startar Streamlit och öppnar webbläsaren. |
| `dashboard.py` | Gränssnittet. |
| `modell.py` | Dos–responsmodellen: sjukindex, modellrader och den långsamma formen. |
| `planering.py` | Veckoplaneraren. |
| `sync.py` / `oura.py` | Hämtning från Garmin Connect respektive Oura API v2. |
| `jobs.py` | Hämtning i bakgrunden. |
| `db.py` | SQLite-databasen (`data/hrv.db`). |
| `importera.py` | Läser in en befintlig `garmin.db`. |
| `bidrag.py` | Bygger bidragsfilen. |
| `demo_data.py` | Påhittad person med känd dos–respons, för att prova appen och för att kontrollera modellen. |
| `updater.py` / `version.py` | Självuppdatering från GitHub-releaser. |
| `skapa_paket.py` | Bygger release-zippen. |
| `prov/` | Testskript: fejkad Oura-server och dashboarden körd headless. |

## Utveckling

    python modell.py data/hrv.db                 # kör modellen och skriv ut nyckeltalen
    python demo_data.py                          # exempelperson; modellen ska återfinna sanningen
    python prov/prov_oura_mock.py
    python prov/prov_dashboard_oura.py
    python skapa_paket.py                        # → dist/HRV-data-<version>.zip

Ny version: höj `VERSION` i `version.py`, kör testskripten och `skapa_paket.py`, och ladda upp zippen
som asset på en release med taggen `v<version>`. Appen hittar den själv. Höj `MODELLVERSION` i
`modell.py` när modellen ändras, så att bidrag från olika modeller går att skilja åt.
