# -*- coding: utf-8 -*-
"""Regelbaserade texter per fenotyp, ur en bidragsfil (dict från bidrag.bygg).

Flyttad hit ur rapport_bidrag.py 2026-10-29 (punkt 13) så att appen kan visa samma texter i flikarna:
rapport_bidrag.py följer inte med i paketet och kräver matplotlib. rapport_bidrag.py importerar härifrån,
så rapporterna är oförändrade. grupp=False tar bort de meningar som jämför med de andra i gruppen.
Texterna innehåller <b>/<i>; markdown() gör om dem för Streamlit."""
import math
import re

MAN = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]


def f(x, n=1, sign=False):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "–"
    s = f"{x:+.{n}f}" if sign else f"{x:.{n}f}"
    return s.replace(".", ",").replace("-", "−")


def tval(c): return c["b"] / c["se"] if c and c.get("se") else float("nan")


def markdown(txt: str) -> str:
    """<b>…</b> → **…**, <i>…</i> → *…* för Streamlit."""
    return re.sub(r"</?i>", "*", re.sub(r"</?b>", "**", txt))


def berattelse(B, alla=None, grupp=True):
    s, kd, kt, tp, F = B["beskrivning"], B["koef_direkt"], B["koef_total"], B["tungt_pass"], B["form"]
    T = {}
    last = kt["load"]["b"] * 100; tl = tval(kt["load"])
    rel = 100 * last / s["hrv_medel"]
    stress1 = kd["stress"]["b"] * s["stress_sd"]; ts = tval(kd["stress"])
    n2 = tp["natt2"]; n2t = n2 / tp["natt2_se"]
    hl = B["halveringstid"]; nv = B.get("nedvarvning") or {}
    nvb = (nv.get("b_igar") or 0) * 100; nvt = (nv.get("b_igar") or 0) / (nv.get("se_igar") or 1)
    hour = kt.get("hour", {}); hb, ht = hour.get("b", 0), tval(hour) if hour else 0
    m = F["manad"]; topp = max(m, key=m.get); botten = min(m, key=m.get)
    sjuk = kt["sjuk"]["b"]
    styrka = kt.get("smin", {}).get("b")

    # last
    if abs(tl) < 2:
        T["last"] = (f"Din nattliga HRV reagerar <b>inte mätbart</b> på träningsdosen: {f(last, 1, True)} ms per 100 enheter "
                     f"last, vilket inte går att skilja från noll. Ett tungt pass (i snitt {tp['medellast']:.0f} i last) ger "
                     f"{f(tp['natt1'], 1, True)} ms natten efter, med ett osäkerhetsintervall som täcker noll. Det betyder "
                     f"inte att träningen är gratis, det betyder att <b>din HRV inte är mätaren för den kostnaden</b>. Senor, "
                     f"leder och ackumulerad trötthet syns inte i nattens RMSSD, och det finns inget i de här siffrorna som "
                     f"säger att hårda pass varje dag är fritt fram.")
        T["last_kort"] = "ingen mätbar träningskostnad i natten"
    else:
        grad = "stor" if rel < -6 else ("tydlig" if rel < -3 else "måttlig")
        T["last"] = (f"Din träning har en <b>{grad}, förutsägbar kostnad</b> i natten: {f(last, 1, True)} ms per 100 enheter "
                     f"last ({f(rel, 1, True)} % av din nivå), och dos–responstrappan är monoton, dvs. hårdare pass kostar "
                     f"mer hela vägen upp. Ett tungt pass (i snitt {tp['medellast']:.0f} i last) ger {f(tp['natt1'], 1, True)} ms "
                     f"natten efter mot en vilodag. Eftersom startvärdet är lika högt oavsett hur hårt passet blev är det "
                     f"dosen som syns, inte att du råkar träna hårt på dina pigga dagar.")
        T["last_kort"] = f"träning kostar {f(abs(last), 1)} ms per 100 last"
    # natt 2
    if n2t > 2 and n2 > 1:
        T["natt2"] = (f"Natt 2 efter ett tungt pass ligger du <b>{f(n2, 1)} ms över</b> en vilodag. Det är en studs, "
                      f"en liten superkompensation i det autonoma systemet, som inte alla har.")
    elif n2t < -2:
        T["natt2"] = (f"Natt 2 efter ett tungt pass är du fortfarande <b>{f(abs(n2), 1)} ms under</b> en vilodag. Du betalar "
                      f"alltså i två nätter, inte en. Dygnet efter ett hårt pass är inte ett grönt ljus för nästa.")
    else:
        T["natt2"] = (f"Natt 2 efter ett tungt pass är du tillbaka ({f(n2, 1, True)} ms mot vilodag, inte skilt från noll). "
                      f"Kostnaden är utlånad och återbetald inom två dygn.")
    # stress
    if abs(ts) < 2:
        T["stress"] = (f"Dagsstressen enligt klockan säger nästan ingenting om din natt ({f(stress1, 1, True)} ms för en dag "
                       f"som ligger en standardavvikelse över din vanliga). Antingen är dina dagar jämna, eller så ligger "
                       f"din belastning på tider som klockans dagsmedel inte fångar, till exempel kvällar och helger.")
    else:
        T["stress"] = (f"En dag med stress en standardavvikelse över din vanliga (+{s['stress_sd']:.0f} enheter) kostar "
                       f"<b>{f(abs(stress1), 1)} ms</b> samma natt. Det är posten som ligger på <i>varje</i> natt den varar, "
                       f"till skillnad från träningen som är en engångskostnad, och därför ofta den största styrbara spaken. "
                       f"Talet är en prognos från klockans dagsmedel, som också räknar stressen under sömnen fram till "
                       f"uppvaknandet. En del av det är alltså föregående natts tillstånd, inte bara dagens belastning.")
    # form
    if hl <= 2:
        T["form"] = (f"Du har <b>ingen långsam form</b> att tala om: det bästa fönstret för ett efterhängande tillstånd är "
                     f"{hl} dygn, och att lägga till formen höjer modellens träffsäkerhet marginellt (R² "
                     f"{f(B['r2']['bas'], 2)} → {f(B['r2']['form'], 2)}). Din natt bestäms av de senaste dagarna, inte av "
                     f"de senaste veckorna. Däremot ligger din natt nära ditt 7-dygnssnitt (koefficient {f(kt['h7']['b'], 2)}), "
                     f"så det snittet är en bra avläsning av var du står.")
    elif hl >= 14:
        T["form"] = (f"Din långsamma form är <b>mycket långsam och mycket liten</b>: halveringstid {hl} dygn, spridning bara "
                     f"{f(F['sd'], 1)} ms, och den rör sig över månader snarare än veckor. Det som är kvar när modellen räknat "
                     f"av träning, stress och form är rent brus (autokorrelation {f(B['residual_acf1'], 2)}). Din kropp "
                     f"håller alltså en jämn nivå och svarar på det som händer de närmaste dygnen.")
    else:
        T["form"] = (f"Du har en <b>långsam form</b> med halveringstid {hl} dygn och spridning {f(F['sd'], 1)} ms. Den "
                     f"ackumulerar nätter: ingen enskild natt kan kvittera den, men två veckor över snittet lyfter den och "
                     f"en belastad period sänker den. Den syns i var natten startar, inte i hur mycket den klättrar.")
    T["sasong"] = (f"Över året ligger formen högst i <b>{MAN[int(topp) - 1]}</b> ({f(m[topp], 1, True)} ms) och lägst i "
                   f"<b>{MAN[int(botten) - 1]}</b> ({f(m[botten], 1, True)} ms). "
                   + ("I gruppen har alla olika säsongsmönster, så det här är ditt, inte årstidens." if grupp
                      else "Det här är ditt mönster, inte nödvändigtvis årstidens."))
    # nedvarvning
    if nvt > 2:
        T["nedvarv"] = (f"Gårdagens träning <b>höjer</b> din stress i dag ({f(nvb, 2, True)} per 100 last). "
                        + ("Det är ovanligt i gruppen, där träning annars ger en lugnare dag efter, och kan betyda "
                           if grupp else "Det kan betyda ")
                        + f"att dagen efter ett hårt pass "
                        f"bär en trötthet som klockan läser som stress. Dubbelpass och täta hårda dagar är det första att "
                        f"titta på.")
    elif nvt < -2:
        T["nedvarv"] = (f"Gårdagens träning <b>sänker</b> din stress i dag ({f(nvb, 2, True)} per 100 last). Träning ger dig "
                        f"en lugnare dag efter, oavsett vad den kostade i natten.")
    else:
        T["nedvarv"] = "Gårdagens träning påverkar inte dagens stressnivå mätbart."
    if ht < -2:
        T["timme"] = f"Senare pass kostar mer: {f(hb, 2, True)} ms per timme senare start. Vill du skydda natten, lägg det hårda tidigare."
    elif ht > 2:
        T["timme"] = f"Tidpunkten på dygnet spelar ingen negativ roll för dig, snarare tvärtom ({f(hb, 2, True)} ms per timme senare start). Kvällspass går bra."
    else:
        T["timme"] = "Tidpunkten på dygnet spelar ingen mätbar roll för din natt."
    T["sjuk"] = (f"Dygn som modellens sjukflagga pekar ut (vilopuls och andning över din egen baslinje) ligger "
                 f"{f(sjuk, 1, True)} ms. I förhållande till din spridning ({f(s['hrv_sd'], 1)} ms) är det "
                 f"{'en stor' if abs(sjuk) > 0.5 * s['hrv_sd'] else 'en måttlig'} post, och den träffar även pass som ser lätta ut.")
    if styrka is not None and abs(tval(kt["smin"])) > 2:
        T["styrka"] = (f"Styrka: {f(styrka * 10, 2, True)} ms per 10 minuter, dvs. ett 45-minuterspass ≈ {f(styrka * 45, 1, True)} ms. "
                       f"Garmins last underskattar styrkans autonoma kostnad, men styrkan bär skadeprevention som HRV inte mäter. "
                       f"Den ska budgeteras, inte optimeras bort.")
    else:
        T["styrka"] = "Styrketräning har ingen mätbar egen kostnad i din natt utöver den last klockan ger den."

    # råd, fenotypberoende
    rad = []
    if abs(tl) < 2:
        rad.append("<b>Använd inte HRV som bromsljus för träningen.</b> Din natt säger inte ifrån när dosen är hög, så planera hårda dagar efter en fast regel (till exempel högst två hårda pass i rad) i stället för efter morgonens HRV.")
        rad.append("<b>Lyssna på andra signaler för träningsslitage:</b> vilopuls, hur benen känns, sömnens längd. De bär det HRV inte gör hos dig.")
    else:
        rad.append(f"<b>Räkna med natten efter.</b> Ett tungt pass kostar dig {f(abs(tp['natt1']), 0)} ms, lugna pass nästan inget. Det är intensiteten som betalar, inte minuterna.")
        if n2t < -2:
            rad.append("<b>Två nätter, inte en.</b> Lägg nästa hårda pass tidigast på dygn 3 om natten ska vara tillbaka.")
        else:
            rad.append("<b>Dygn 2 är ärligare än dygn 1.</b> Natten direkt efter är nedtryckt oavsett, så läs av formen först morgonen därpå.")
    if abs(ts) >= 2:
        rad.append(f"<b>Stressen är spaken.</b> Den ligger på varje natt den varar. En stressig vecka kostar mer än ett hårt pass, och den enda som kan sänka den är du.")
    else:
        rad.append("<b>Klockans dagsstress är inte din stress.</b> Om din belastning ligger på kvällar och helger syns den inte i dagsmedlet. Titta på nattens HRV veckodag mot helg i stället.")
    if hl >= 14:
        rad.append("<b>Din form rör sig långsamt.</b> Läs 7-dygnssnittet, inte enskilda nätter, och förvänta dig att en belastad period syns i veckor.")
    elif hl <= 2:
        rad.append("<b>Din natt är kort i minnet.</b> En dålig vecka är över när den är över. Det är en styrka, men också en anledning att inte övertolka enskilda nätter.")
    else:
        rad.append("<b>Läs trenden, inte natten.</b> Den långsamma formen ackumulerar två veckor. En enskild låg natt är en datapunkt, inte en dom.")
    if nvt > 2:
        rad.append("<b>Dagen efter ett hårt pass är belastad hos dig.</b> Lägg inte möteskaos eller resa dagen efter de tyngsta passen om du kan välja.")
    rad.append("<b>Ett högt HRV är inget mål i sig.</b> Det som är värt att läsa är avvikelserna nedåt från din egen nivå, hur djupa, hur länge, och vad de sammanfaller med.")
    T["rad"] = rad
    return T
