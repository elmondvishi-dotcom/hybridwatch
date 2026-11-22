"""Generate a HELD-OUT synthetic 48-hour scenario (new stories, places, claims, outlets) to test HybridWatch on data
it was never tuned on. Run once; do not change thresholds or keyword lists to fit it.

    py scripts/make_holdout_data.py      -> data/holdout/{posts.jsonl, cyber.json, factchecks.json}
    py scripts/evaluate_holdout.py       -> results
"""
from __future__ import annotations

import json
import random
from pathlib import Path

random.seed(777)
START = 1794096000  # 2026-11-08 00:00 UTC (a different week from the demo)
H = 3600.0
DAY = 86400.0
OUT = Path(__file__).resolve().parent.parent / "data" / "holdout"

_LAT2CYR = [("dž", "џ"), ("lj", "љ"), ("nj", "њ"), ("Lj", "Љ"), ("Nj", "Њ"), ("Dž", "Џ")] + list(zip(
    "abvgdđežzijklmnoprstćufhcčšABVGDĐEŽZIJKLMNOPRSTĆUFHCČŠ",
    "абвгдђежзијклмнопрстћуфхцчшАБВГДЂЕЖЗИЈКЛМНОПРСТЋУФХЦЧШ"))


def to_cyrillic(s: str) -> str:
    out = []
    for word in s.split(" "):
        if word.startswith(("#", "http")):
            out.append(word)
            continue
        for a, b in _LAT2CYR:
            word = word.replace(a, b)
        out.append(word)
    return " ".join(out)


# ------------------------------------------------------------------ accounts
SQ_NAMES = ["arben", "blerta", "dren", "liridona", "gent", "era", "valon", "vjosa", "besnik", "drita", "ilir", "teuta",
            "fatos", "albana", "kushtrim", "rina", "agon", "elira", "shpend", "lule"]
SR_NAMES = ["marko", "jelena", "nikola", "milica", "stefan", "ana", "dusan", "ivana", "milos", "jovana", "nemanja",
            "tamara", "luka", "sara", "vuk", "maja", "filip", "nina", "petar", "teodora"]
EN_NAMES = ["balkanwatcher", "eu_observer", "kosovo_analyst", "regional_reporter", "travel_balkans", "student_pr"]

accounts: dict[str, dict] = {}


def make_account(handle, created, followers, lang):
    accounts[handle] = {"handle": handle, "created": created, "followers": int(followers), "lang": lang}
    return handle


organic = {"sq": [], "sr": [], "en": []}
for i in range(260):
    organic["sq"].append(make_account(f"{random.choice(SQ_NAMES)}_{random.randint(10, 9999)}",
                                      START - random.uniform(400, 3600) * DAY, random.lognormvariate(5.5, 1.2), "sq"))
for i in range(220):
    organic["sr"].append(make_account(f"{random.choice(SR_NAMES)}{random.randint(10, 9999)}",
                                      START - random.uniform(400, 3600) * DAY, random.lognormvariate(5.5, 1.2), "sr"))
for i in range(90):
    organic["en"].append(make_account(f"{random.choice(EN_NAMES)}{random.randint(1, 999)}",
                                      START - random.uniform(400, 3600) * DAY, random.lognormvariate(6, 1.2), "en"))

# coordinated network: mostly brand-new, low-follower accounts (+ a few aged "sleeper" accounts)
network = []
for i in range(70):
    new = random.random() < 0.8
    created = START - (random.uniform(4, 25) if new else random.uniform(200, 900)) * DAY
    prefix = random.choice(["news_now", "real_truth", "info_live", "glas_naroda", "zeri_popullit", "breaking_bk",
                            "istina24", "e_verteta", "alarm_info", "narod_zna"])
    network.append(make_account(f"{prefix}_{random.randint(100, 9999)}", created, random.uniform(15, 450), "mix"))

OUTLETS = {
    "Dukagjini Today (holdout)": ("sq", "credible", 70000),
    "Adriatic Report (holdout)": ("en", "credible", 110000),
    "Glas Severa (holdout)": ("sr", "credible", 50000),
    "Lajmi Online (holdout)": ("sq", "credible", 90000),
}
LOWCRED_CHANNELS = {"Alarm Kosova (holdout)": ("sq", 12000), "Pravda Sever (holdout)": ("sr", 8000),
                    "Balkan Leaks (holdout)": ("en", 5000)}
for name, (lang, cred, fol) in OUTLETS.items():
    make_account(name, START - 3000 * DAY, fol, lang)
for name, (lang, fol) in LOWCRED_CHANNELS.items():
    make_account(name, START - 60 * DAY, fol, lang)

posts: list[dict] = []
_seq = 0


def emit(author, ts, text, en, lang, narrative, source_type="x", credibility="unknown",
         coordinated=False, debunk=None, cyr_ok=True):
    global _seq
    _seq += 1
    if lang == "sr" and cyr_ok and random.random() < 0.4:
        text = to_cyrillic(text)
    acc = accounts[author]
    posts.append({
        "ext_id": f"demo-{_seq:05d}", "source": author if source_type in ("news", "telegram") else f"{source_type}/{author}",
        "source_type": source_type, "credibility": credibility, "author": author,
        "author_created": acc["created"], "followers": acc["followers"], "ts": round(ts, 1),
        "text": text, "translation": None if lang == "en" else en, "lang": lang,
        "url": f"https://example.org/demo/{_seq}",
        "gt_narrative": narrative, "gt_coordinated": coordinated, "gt_debunk": debunk,
    })


def pick(seq):
    return random.choice(seq)


FILLERS = [("sinqerisht", "iskreno", "honestly"), ("hajde", "ajde", "come on"), ("ufff", "uh", "ugh"),
           ("tani", "sada", "right now"), ("prap", "opet", "again"), ("lol", "lol", "lol"),
           ("vëllezër", "ljudi", "people"), ("o zot", "bože", "god"), ("sot", "danas", "today"),
           ("mendoj", "mislim", "I think"), ("me të vërtetë", "stvarno", "really"), ("në fakt", "u stvari", "actually"),
           ("çfarë dite", "kakav dan", "what a day"), ("hmm", "hmm", "hmm"), ("nuk po besoj", "ne verujem", "can't believe it")]


def compose(parts: list[list[tuple[str, str, str]]], lang: str) -> tuple[str, str]:
    """parts: slots, each a list of (sq, sr, en) variants. Returns (text, english).
    Organic posts vary a lot: parts get dropped, reordered and mixed with personal fillers."""
    idx = {"sq": 0, "sr": 1, "en": 2}[lang]
    chosen = [pick(slot) for slot in parts]
    if len(chosen) > 2 and random.random() < 0.35:
        chosen.pop(random.randrange(1, len(chosen)))
    if len(chosen) > 2 and random.random() < 0.3:
        i = random.randrange(1, len(chosen) - 1)
        chosen[i], chosen[i + 1] = chosen[i + 1], chosen[i]
    for _ in range(random.choice([0, 1, 1, 2])):
        chosen.insert(random.randrange(len(chosen) + 1), pick(FILLERS))
    return " ".join(c[idx] for c in chosen if c[idx]), " ".join(c[2] for c in chosen if c[2])


def organic_wave(narrative, t0, t1, n, parts, langs=("sq", "sr", "en"), weights=(0.45, 0.4, 0.15),
                 shape="burst", debunk=None, platform=("x", "facebook", "telegram")):
    for _ in range(n):
        if shape == "burst":   # fast rise, slow decay
            ts = t0 + (t1 - t0) * min(random.expovariate(3.0), 1.0)
        else:
            ts = random.uniform(t0, t1)
        lang = random.choices(langs, weights)[0]
        text, en = compose(parts, lang)
        if random.random() < 0.15:
            text += " " + pick(["😡", "😮", "🙏", "!!", "?", "😔", "👀"])
        emit(pick(organic[lang]), ts, text, en, lang, narrative, source_type=pick(platform), debunk=debunk)


def news(outlet, ts, text, en, narrative):
    lang = OUTLETS[outlet][0]
    emit(outlet, ts, text, en, lang, narrative, source_type="news", credibility="credible", cyr_ok=False)


def campaign_burst(narrative, t0, templates, n_accounts, minutes, debunk=None, reuse=None):
    """Copy-paste burst: each account posts a near-identical template with tiny edits."""
    accts = random.sample(reuse or network, n_accounts)
    for a in accts:
        lang = random.choices(["sr", "sq", "en"], [0.45, 0.4, 0.15])[0]
        text, en = templates[lang]
        words = text.split()
        if random.random() < 0.3 and len(words) > 6:  # one-word edit
            j = random.randrange(len(words))
            words[j] = words[j].upper() if random.random() < 0.5 else words[j] + "!"
        text = " ".join(words)
        text += " " + random.choice(["", "", "‼️", "⚠️", "#share", "#hitno", "#urgjent", "🔴"])
        emit(a, t0 + random.uniform(0, minutes * 60), text.strip(), en, lang, narrative,
             source_type=random.choice(["x", "telegram", "facebook"]), coordinated=True, debunk=debunk)
    return accts



# =================================================================== HELD-OUT SCENARIO
# Written AFTER the system was built, with new stories, places, claims and outlets, and run ONCE
# without changing any threshold, keyword list or rule. Nothing here was used to tune HybridWatch.
#
#   03-09  water_cut_gjilan   real water cut in Gjilan (burst main pipe), organic        -> infrastructure, Gjilan
#   06-44  remittance_tax     old debunked rumour (20% tax on diaspora money), organic   -> disinformation (debunk match)
#   09-22  flood_prizren      real flooding in Prizren, organic, fast growth            -> infrastructure, Prizren
#   16-24  school_vaccine     CAMPAIGN: "children poisoned by a vaccine in Ferizaj"      -> disinformation (debunked)
#   20-26  black_friday       shopping spike, harmless                                  -> no threat event
#   24-30  shots_zubin_potok  shots fired at a police patrol                             -> security incident, Zubin Potok
#   26-30  heroin_podujeva    police seize heroin, three arrested                        -> organized activity, Podujeva
#   29-37  police_north       CAMPAIGN: "Kosovo Police leaving the north" (same network) -> disinformation (debunked)
#   33-39  gracanica_protest  protest blocks the main road in Gračanica                   -> organized activity, Gračanica
#   37-42  bank_outage        real card/ATM outage at a bank, organic                    -> infrastructure, Pristina
#   37-41  bank_run           CAMPAIGN distorting the outage: "banks are collapsing"     -> disinformation + HYBRID link
#   40-47  rs_institutions    Republika Srpska parliament leaves state institutions     -> regional
#   all    rent_prices (harmless), noise
#   CYBER  defacement (confirmed), DDoS claim on customs (refuted), KEV FortiOS (confirmed),
#          ransomware at a clinic (confirmed), bank outage (confirmed, hybrid), re-posted voter-data leak (refuted)

# ------------------------------------------------------------------ 1. water cut in Gjilan (organic)
T = START + 3 * H
news("Lajmi Online (holdout)", T, "Gjilan: disa lagje pa ujë pas plasjes së gypit kryesor, riparimi pritet deri në mbrëmje.",
     "Gjilan: several neighbourhoods without water after the main pipe burst, repair expected by evening.", "water_cut_gjilan")
news("Adriatic Report (holdout)", T + 0.5 * H, "Burst main pipe leaves parts of Gjilan without running water; utility says repairs by tonight.",
     "", "water_cut_gjilan")
organic_wave("water_cut_gjilan", T + 0.1 * H, T + 6 * H, 110, [
    [("Pa ujë në Gjilan", "Nema vode u Gnjilanu", "No water in Gjilan"), ("Na u ndal uji", "Nestalo vode", "Our water just stopped"),
     ("Sërish pa ujë", "Opet bez vode", "Without water again")],
    [("që nga mëngjesi", "od jutros", "since this morning"), ("në lagjen tonë", "u našem kraju", "in our neighbourhood"),
     ("në qendër", "u centru", "in the centre"), ("", "", "")],
    [("Thonë që plasi gypi kryesor.", "Kažu da je pukla glavna cev.", "They say the main pipe burst."),
     ("Kur kthehet uji?", "Kada će doći voda?", "When is the water back?"), ("Po blejmë ujë në dyqan.", "Kupujemo vodu u radnji.",
      "Buying water at the shop."), ("", "", "")]])

# ------------------------------------------------------------------ 2. remittance tax rumour (old, debunked, organic)
organic_wave("remittance_tax", START + 6 * H, START + 44 * H, 50, [
    [("Kujdes,", "Pazite,", "Careful,"), ("Dëgjova që", "Čuo sam da", "I heard that"), ("", "", "")],
    [("qeveria do të vendosë taksë 20% në paratë që dërgon diaspora",
      "vlada uvodi porez od 20% na novac koji šalje dijaspora",
      "the government will put a 20% tax on money sent by the diaspora"),
     ("remitancat nga jashtë do të tatohen me 20%", "doznake iz inostranstva biće oporezovane 20%",
      "remittances from abroad will be taxed 20%")],
    [("nga viti i ri", "od nove godine", "from the new year"), ("së shpejti", "uskoro", "soon"), ("", "", "")],
    [("A është e vërtetë?", "Da li je to istina?", "Is this true?"), ("Skandal!", "Skandal!", "Scandal!"), ("", "", "")]],
    shape="flat", debunk="hold-fc-3")

# ------------------------------------------------------------------ 3. flooding in Prizren (organic, fast growth)
T = START + 9 * H
news("Dukagjini Today (holdout)", T, "Reshjet e shiut vërshojnë rrugë dhe bodrume në Prizren, Lumbardhi doli nga shtrati.",
     "Heavy rain floods streets and basements in Prizren, the Lumbardhi river burst its banks.", "flood_prizren")
news("Glas Severa (holdout)", T + 0.6 * H, "Poplave u Prizrenu, reka Bistrica se izlila, poplavljeni podrumi.",
     "Floods in Prizren, the Bistrica river overflowed, basements flooded.", "flood_prizren")
organic_wave("flood_prizren", T + 0.2 * H, T + 13 * H, 260, [
    [("Përmbytje në Prizren", "Poplava u Prizrenu", "Flooding in Prizren"), ("Shi i pandalshëm", "Kiša ne prestaje", "Non-stop rain"),
     ("Lumbardhi doli nga shtrati", "Bistrica se izlila", "The river burst its banks")],
    [("bodrumet plot ujë", "podrumi puni vode", "basements full of water"), ("rrugët janë lumenj", "ulice su reke", "the streets are rivers"),
     ("veturat nën ujë", "automobili pod vodom", "cars under water"), ("te Shadërvani", "kod Šadrvana", "near Shadervan")],
    [("Kujdes!", "Pazite se!", "Be careful!"), ("Fotot nga sot", "Slike od danas", "Photos from today"), ("", "", ""), ("", "", "")]])

# ------------------------------------------------------------------ 4. school vaccine CAMPAIGN (recycled debunked claim)
T = START + 16 * H
emit("Pravda Sever (holdout)", T - 0.4 * H, "HITNO: deca u školama u Uroševcu se razbolela od otrovne vakcine, ministarstvo krije!",
     "URGENT: children in Ferizaj schools fell ill from a poisoned vaccine, the ministry is hiding it!", "sr",
     "school_vaccine", source_type="telegram", credibility="low", debunk="hold-fc-1")
emit("Alarm Kosova (holdout)", T - 0.3 * H, "URGJENT: fëmijët në shkollat e Ferizajt u sëmurën nga një vaksinë e helmuar, ministria e fsheh!",
     "URGENT: children in Ferizaj schools got sick from a poisoned vaccine, the ministry hides it!", "sq",
     "school_vaccine", source_type="telegram", credibility="low", debunk="hold-fc-1")
VAX_T = {
    "sr": ("HITNO: deca u školama u Uroševcu otrovana vakcinom, ministarstvo krije istinu! Ne vodite decu na vakcinaciju, podelite.",
           "URGENT: children in Ferizaj schools poisoned by a vaccine, the ministry hides the truth! Don't take kids to be vaccinated, share."),
    "sq": ("URGJENT: fëmijët në shkollat e Ferizajt u helmuan nga vaksina, ministria e fsheh të vërtetën! Mos i çoni fëmijët në vaksinim.",
           "URGENT: children in Ferizaj schools were poisoned by the vaccine, the ministry hides the truth! Don't take kids to vaccination."),
    "en": ("URGENT: kids in Ferizaj schools poisoned by a vaccine and the ministry is hiding it! Do not let your children get vaccinated, share.", ""),
}
vwave = campaign_burst("school_vaccine", T, VAX_T, 44, 12, debunk="hold-fc-1")
campaign_burst("school_vaccine", T + 2 * H, VAX_T, 20, 15, debunk="hold-fc-1", reuse=vwave)
organic_wave("school_vaccine", T + 0.3 * H, T + 8 * H, 70, [
    [("A është e vërtetë që", "Da li je tačno da", "Is it true that"), ("Po shoh në Facebook që", "Vidim na Fejsbuku da", "I'm seeing on Facebook that")],
    [("fëmijët në Ferizaj u helmuan nga vaksina", "su deca u Uroševcu otrovana vakcinom", "children in Ferizaj were poisoned by a vaccine"),
     ("vaksina në shkolla është e rrezikshme", "je vakcina u školama opasna", "the school vaccine is dangerous")],
    [("?", "?", "?"), ("Kam frikë për djalin.", "Plašim se za sina.", "Scared for my son."), ("Dikush e di?", "Zna li neko?", "Anyone know?"), ("", "", "")]],
    debunk="hold-fc-1")
news("Lajmi Online (holdout)", T + 3 * H, "Ministria e Shëndetësisë: lajmet për fëmijë të helmuar nga vaksina në Ferizaj janë të rreme.",
     "Ministry of Health: reports of children poisoned by a vaccine in Ferizaj are false.", "school_vaccine")
news("Adriatic Report (holdout)", T + 3.4 * H, "Health ministry denies claims that a school vaccine made children ill in Ferizaj.", "", "school_vaccine")

# ------------------------------------------------------------------ 5. Black Friday (harmless spike)
organic_wave("black_friday", START + 20 * H, START + 26 * H, 220, [
    [("Black Friday", "Black Friday", "Black Friday"), ("Zbritjet e sotme", "Današnji popusti", "Today's discounts"),
     ("Ofertat në Albi Mall", "Akcije u tržnom centru", "Deals at the mall")],
    [("50% në të gjitha", "50% na sve", "50% off everything"), ("radha e gjatë te dyqani", "dugačak red ispred radnje",
     "long queue outside the shop"), ("bleva një televizor", "kupio sam televizor", "bought a TV"), ("", "", "")],
    [("🛍️", "🛍️", "🛍️"), ("Ia vlen?", "Isplati li se?", "Worth it?"), ("", "", "")]])

# ------------------------------------------------------------------ 6. shots at a police patrol near Zubin Potok
POLICE = make_account("Kosovo Police (holdout)", START - 3000 * DAY, 150000, "sq")


def official(author, ts, text, en, narrative, lang="sq"):
    emit(author, ts, text, en, lang, narrative, source_type="official", credibility="credible", cyr_ok=False)


T = START + 24 * H
news("Glas Severa (holdout)", T + 20 * 60, "Pucnji na patrolu Kosovske policije kod Zubinog Potoka, nema povređenih.",
     "Shots fired at a Kosovo Police patrol near Zubin Potok, no one injured.", "shots_zubin_potok")
official(POLICE, T + 45 * 60, "Një patrullë e policisë u qëllua me armë zjarri afër Zubin Potokut. Nuk ka të lënduar, hetimet vazhdojnë.",
         "A police patrol was shot at near Zubin Potok. No one was hurt, investigation ongoing.", "shots_zubin_potok")
news("Adriatic Report (holdout)", T + 60 * 60, "Gunmen open fire on a Kosovo Police patrol near Zubin Potok; no injuries reported.", "",
     "shots_zubin_potok")
organic_wave("shots_zubin_potok", T + 10 * 60, T + 6 * H, 40, [
    [("U qëllua patrulla e policisë te Zubin Potoku", "Pucano na policijsku patrolu kod Zubinog Potoka", "Police patrol shot at near Zubin Potok"),
     ("Të shtëna me armë afër Zubin Potokut", "Pucnjava kod Zubinog Potoka", "Gunfire near Zubin Potok")],
    [("askush nuk u lëndua", "niko nije povređen", "nobody hurt"), ("autorët ikën", "napadači pobegli", "attackers fled"), ("", "", "")],
    [("Tension në veri përsëri.", "Opet napetost na severu.", "Tension in the north again."), ("", "", "")]])

# ------------------------------------------------------------------ 7. heroin seizure near Podujeva (organised crime)
T = START + 26 * H
official(POLICE, T, "Policia sekuestroi 40 kg heroinë dhe arrestoi tre persona në një pikë kontrolli afër Podujevës.",
         "Police seized 40 kg of heroin and arrested three people at a checkpoint near Podujeva.", "heroin_podujeva")
news("Lajmi Online (holdout)", T + 0.6 * H, "Kapen 40 kilogramë heroinë afër Podujevës, tre të arrestuar.",
     "40 kilograms of heroin seized near Podujeva, three arrested.", "heroin_podujeva")
news("Adriatic Report (holdout)", T + 0.9 * H, "Kosovo police seize 40 kg of heroin near Podujeva and arrest three suspected traffickers.", "",
     "heroin_podujeva")
organic_wave("heroin_podujeva", T + 0.7 * H, T + 4 * H, 10, [
    [("40 kg heroinë te Podujeva", "40 kg heroina kod Podujeva", "40 kg of heroin near Podujeva")],
    [("tre të arrestuar", "trojica uhapšena", "three arrested"), ("bravo policisë", "bravo policija", "well done police")]])

# ------------------------------------------------------------------ 8. Kosovo Police leaving the north CAMPAIGN (same network)
T = START + 29 * H
NORTH_T = {
    "sr": ("Kosovska policija napušta sever sledećeg meseca, srpska policija se vraća, potvrđeno iz EU izvora. Spremite se!",
           "Kosovo Police are leaving the north next month, Serbian police are returning, confirmed by EU sources. Get ready!"),
    "sq": ("Policia e Kosovës largohet nga veriu muajin tjetër, kthehet policia serbe, konfirmuar nga burime të BE-së. Përgatituni!",
           "Kosovo Police leave the north next month, Serbian police return, confirmed by EU sources. Get ready!"),
    "en": ("Kosovo Police will leave the north next month and Serbian police are coming back, EU sources confirm. Get ready!", ""),
}
reuse = random.sample(vwave, 28) + random.sample([a for a in network if a not in vwave], 10)
campaign_burst("police_north", T, NORTH_T, 36, 14, debunk="hold-fc-2", reuse=reuse)
organic_wave("police_north", T + 0.5 * H, T + 7 * H, 40, [
    [("A po largohet vërtet policia nga veriu?", "Da li se policija stvarno povlači sa severa?", "Are the police really leaving the north?"),
     ("Lexova që policia serbe kthehet në veri", "Pročitao sam da se srpska policija vraća", "I read Serbian police are returning to the north")],
    [("muajin tjetër", "sledećeg meseca", "next month"), ("", "", "")],
    [("Burim?", "Izvor?", "Source?"), ("Kjo s'ka kuptim.", "Ovo nema smisla.", "This makes no sense."), ("", "", "")]],
    debunk="hold-fc-2")
news("Adriatic Report (holdout)", T + 3.5 * H, "Kosovo government and EU deny reports that Kosovo Police will withdraw from the north.", "",
     "police_north")

# ------------------------------------------------------------------ 9. protest blocking the road in Gračanica
T = START + 33 * H
news("Glas Severa (holdout)", T + 0.3 * H, "Stotine ljudi blokirale glavni put u Gračanici u znak protesta zbog hapšenja meštanina.",
     "Hundreds of people blocked the main road in Gračanica in protest over the arrest of a local man.", "gracanica_protest")
news("Lajmi Online (holdout)", T + 0.6 * H, "Qindra protestues bllokuan rrugën kryesore në Graçanicë pas arrestimit të një banori.",
     "Hundreds of protesters blocked the main road in Gračanica after a resident was arrested.", "gracanica_protest")
organic_wave("gracanica_protest", T, T + 6 * H, 55, [
    [("Protestë në Graçanicë", "Protest u Gračanici", "Protest in Gračanica"),
     ("Rruga kryesore në Graçanicë e bllokuar nga protestuesit", "Demonstranti blokirali glavni put u Gračanici",
      "Protesters blocked the main road in Gračanica")],
    [("qindra njerëz", "stotine ljudi", "hundreds of people"), ("për shkak të arrestimit", "zbog hapšenja", "over the arrest"), ("", "", "")],
    [("Policia është në vend.", "Policija je na licu mesta.", "Police are there."), ("", "", "")]])

# ------------------------------------------------------------------ 10. RS leaves state institutions (regional)
T = START + 40 * H
news("Glas Severa (holdout)", T, "Narodna skupština Republike Srpske izglasala izlazak iz institucija BiH.",
     "The Republika Srpska National Assembly voted to withdraw from the institutions of Bosnia and Herzegovina.", "rs_institutions")
news("Adriatic Report (holdout)", T + 0.5 * H, "Republika Srpska parliament votes to pull out of Bosnia's state institutions; EU warns of crisis.", "",
     "rs_institutions")
news("Dukagjini Today (holdout)", T + 1 * H, "Kuvendi i Republikës Serbe votoi daljen nga institucionet e Bosnjës, BE paralajmëron krizë.",
     "The Republika Srpska assembly voted to leave Bosnia's institutions, the EU warns of a crisis.", "rs_institutions")
organic_wave("rs_institutions", T + 0.3 * H, T + 7 * H, 30, [
    [("Republika Serbe del nga institucionet e Bosnjës", "Republika Srpska izlazi iz institucija BiH", "Republika Srpska leaves Bosnia's institutions")],
    [("BE kërkon qetësi", "EU poziva na smirenost", "the EU calls for calm"), ("Dodik e paralajmëroi", "Dodik je najavio", "Dodik had announced it"), ("", "", "")],
    [("Rajoni po nxehet.", "Region se zahuktava.", "The region is heating up."), ("", "", "")]])

# ------------------------------------------------------------------ 11. rent prices (harmless, steady)
organic_wave("rent_prices", START, START + 48 * H, 100, [
    [("Qiratë në Prishtinë", "Kirije u Prištini", "Rents in Pristina"), ("Çmimet e banesave", "Cene stanova", "Flat prices")],
    [("janë çmendur", "su poludele", "have gone crazy"), ("u rritën sërish", "opet porasle", "went up again")],
    [("Si t'ia dalim?", "Kako da živimo?", "How do we cope?"), ("", "", "")]], shape="flat")

# ------------------------------------------------------------------ 12. noise
NOISE_SQ = ["Kafe me shokët", "Kush shkon në kinema sonte", "Provim nesër", "Darkë te gjyshja", "Ecje në Germi", "Libër i ri",
            "Koncert në Prizren", "Kërkoj punë", "Recetë për pite", "Telefoni u prish", "Palestër sot", "Ditëlindje e motrës"]
NOISE_SR = ["Kafa sa drugarima", "Ko ide u bioskop večeras", "Ispit sutra", "Večera kod bake", "Šetnja", "Nova knjiga",
            "Koncert večeras", "Tražim posao", "Recept za pitu", "Pokvario mi se telefon", "Teretana danas", "Sestrin rođendan"]
NOISE_EN = ["Coffee with friends", "Cinema tonight anyone", "Exam tomorrow", "Dinner at grandma's", "Nice walk", "New book",
            "Concert tonight", "Looking for a job", "Pie recipe", "My phone broke", "Gym today", "Sister's birthday"]
for _ in range(1500):
    lang = random.choices(["sq", "sr", "en"], [0.45, 0.4, 0.15])[0]
    base = {"sq": NOISE_SQ, "sr": NOISE_SR, "en": NOISE_EN}[lang]
    i = random.randrange(len(base))
    tail = random.choice(["", "", " :)", " 😊", f" #{random.randint(1, 999)}", " !"])
    emit(pick(organic[lang]), random.uniform(START, START + 48 * H), base[i] + tail, NOISE_EN[i] + tail, lang, "noise",
         source_type=pick(["x", "facebook", "instagram"]))

# ------------------------------------------------------------------ 13. CYBER (recorded checks, no live calls)
M = 60.0
HACK = make_account("holdout_hacktivist_grp", START - 12 * DAY, 3100, "mix")
SYN = " (synthetic)"
cyber_events = []


def cyber_post(author, ts, text, en, lang, narrative, source_type, credibility):
    emit(author, ts, text, en, lang, narrative, source_type=source_type, credibility=credibility, cyr_ok=False)


def probes(t0, pattern, url, name="availability"):
    return [{"check_name": name, "result": r, "detail": d, "evidence_url": url, "checked_at": round(t0 + m * M, 1)}
            for m, r, d in pattern]


# a) defacement of the Gjilan municipality website -> confirmed (site stays up but its content changed)
TA = START + 11 * H
cyber_post(HACK, TA, "Faqja e komunës së Gjilanit u hakua nga ekipi ynë! #defaced", "The Gjilan municipality website was hacked by our team!",
           "sq", "cyber_deface", "telegram", "low")
cyber_post("Lajmi Online (holdout)", TA + 50 * M, "Faqja zyrtare e komunës së Gjilanit u hakua, në ballinë u shfaq një mesazh i hakerëve.",
           "The official Gjilan municipality website was hacked, a hacker message appeared on the homepage.", "sq", "cyber_deface", "news", "credible")
cyber_post(make_account("Municipality of Gjilan (holdout)", START - 3000 * DAY, 30000, "sq"), TA + 90 * M,
           "Faqja e komunës u sulmua dhe u rikthye. Nuk janë prekur të dhëna personale.",
           "The municipality website was attacked and has been restored. No personal data was affected.", "sq", "cyber_deface",
           "official", "credible")
cyber_events.append({
    "ext_id": "hold-cyber-1", "kind": "defacement", "title": "Hacktivists claim defacement of the Gjilan municipality website",
    "target": "Gjilan municipality website", "sector": "government", "claimed_actor": "holdout_hacktivist_grp" + SYN,
    "source_name": "t.me/holdout_hacktivist_grp" + SYN, "source_type": "hacktivist",
    "source_url": "https://example.org/holdout/telegram/1", "claimed_at": TA, "first_seen": TA + 3 * M,
    "raw": {"synthetic": True, "domain": "gjilan.example.org", "keywords": ["gjilan", "gnjilan"], "gt_status": "confirmed",
            "recorded_checks": probes(TA, [(5, "contradicts", "Homepage up (HTTP 200, 0.5 s)"),
                                           (30, "contradicts", "Homepage up (HTTP 200, 0.4 s)")], "https://gjilan.example.org/")
            + probes(TA, [(5, "supports", "Homepage content changed: hacker banner replaces the usual page")],
                     "https://gjilan.example.org/", name="content_hash")}})

# b) DDoS claim on the customs portal that stayed up -> refuted
TB = START + 21 * H
cyber_post(HACK, TB, "Dogana e Kosovës është poshtë! #DDoS", "Kosovo Customs is down! #DDoS", "sq", "cyber_customs_claim", "telegram", "low")
cyber_events.append({
    "ext_id": "hold-cyber-2", "kind": "ddos_claim", "title": "Hacktivists claim DDoS on the Kosovo Customs portal",
    "target": "Kosovo Customs portal", "sector": "government", "claimed_actor": "holdout_hacktivist_grp" + SYN,
    "source_name": "t.me/holdout_hacktivist_grp" + SYN, "source_type": "hacktivist",
    "source_url": "https://example.org/holdout/telegram/2", "claimed_at": TB, "first_seen": TB + 2 * M,
    "raw": {"synthetic": True, "domain": "dogana.example.org", "keywords": ["dogana", "customs", "carina"], "gt_status": "refuted",
            "recorded_checks": probes(TB, [(4, "contradicts", "Homepage up (HTTP 200, 0.4 s)"),
                                           (15, "contradicts", "Homepage up (HTTP 200, 0.5 s)"),
                                           (35, "contradicts", "Homepage up (HTTP 200, 0.4 s)")], "https://dogana.example.org/")}})

# c) actively exploited FortiOS flaw -> confirmed
TC = START + 14 * H
cyber_events.append({
    "ext_id": "hold-cyber-3", "kind": "vulnerability", "title": "CVE-2024-21762 (FortiOS SSL VPN) actively exploited",
    "target": "Fortinet FortiOS SSL VPN", "sector": "other", "claimed_actor": None,
    "source_name": "CISA KEV (recorded)" + SYN, "source_type": "kev",
    "source_url": "https://www.cisa.gov/known-exploited-vulnerabilities-catalog", "claimed_at": TC, "first_seen": TC,
    "raw": {"synthetic": True, "cve": "CVE-2024-21762", "vendor": "Fortinet", "product": "FortiOS", "gt_status": "confirmed",
            "recorded_checks": [{"check_name": "kev", "result": "supports", "checked_at": round(TC, 1),
                                 "detail": "CVE-2024-21762 (FortiOS SSL VPN out-of-bounds write) listed as actively exploited",
                                 "evidence_url": "https://nvd.nist.gov/vuln/detail/CVE-2024-21762"}]}})

# d) new ransomware listing of a Prizren clinic, confirmed by the clinic and the news -> confirmed
TD = START + 27 * H
cyber_post("Dukagjini Today (holdout)", TD + 3 * H, "Klinika Demo Shëndeti në Prizren konfirmon sulm me ransomware, shërbimet funksionojnë me letër.",
           "Demo Health Clinic in Prizren confirms a ransomware attack, services running on paper.", "sq", "cyber_clinic", "news", "credible")
cyber_post(make_account("Demo Health Clinic (holdout)", START - 2000 * DAY, 4000, "sq"), TD + 4 * H,
           "Demo Health Clinic: sistemet tona u goditën nga ransomware. Pacientët do të informohen.",
           "Demo Health Clinic: our systems were hit by ransomware. Patients will be informed.", "sq", "cyber_clinic", "official", "credible")
cyber_events.append({
    "ext_id": "hold-cyber-4", "kind": "ransomware_claim", "title": "Ransomware listing: Demo Health Clinic, Prizren",
    "target": "Demo Health Clinic" + SYN, "sector": "health", "claimed_actor": "DemoCrypt" + SYN,
    "source_name": "Ransomware tracker (recorded)" + SYN, "source_type": "ransomware_tracker",
    "source_url": "https://example.org/holdout/ransomware-tracker/clinic", "claimed_at": TD, "first_seen": TD + 10 * M,
    "raw": {"synthetic": True, "country": "XK", "description": "Private health clinic in Prizren, Kosovo",
            "keywords": ["demo health clinic"], "gt_status": "confirmed"}})

# e) card/ATM outage at a bank, then the coordinated "banks are collapsing" campaign -> HYBRID
TE = START + 37 * H
BANK = "Demo Commerce Bank"
cyber_post("Lajmi Online (holdout)", TE + 25 * M, f"{BANK}: bankomatet dhe pagesat me kartelë nuk funksionojnë në gjithë Kosovën.",
           f"{BANK}: ATMs and card payments are down across Kosovo.", "sq", "bank_outage", "news", "credible")
cyber_post(make_account(f"{BANK} (holdout)", START - 3000 * DAY, 25000, "sq"), TE + 40 * M,
           "Për shkak të një problemi teknik, pagesat me kartelë janë të ndërprera. Mjetet e klientëve janë të sigurta.",
           "Due to a technical problem card payments are interrupted. Customers' funds are safe.", "sq", "bank_outage", "official", "credible")
organic_wave("bank_outage", TE + 5 * M, TE + 5 * H, 70, [
    [("Bankomati nuk punon", "Bankomat ne radi", "The ATM isn't working"), ("Nuk pranohet kartela", "Kartica ne prolazi", "My card is declined"),
     ("Pagesat me kartelë nuk shkojnë", "Plaćanje karticom ne radi", "Card payments are down")],
    [("në Prishtinë", "u Prištini", "in Pristina"), ("te dyqani", "u prodavnici", "at the shop"), ("", "", "")],
    [("Çka po ndodh me bankën?", "Šta se dešava sa bankom?", "What's going on with the bank?"), ("", "", "")]])
RUN_T = {
    "sr": ("Banke propadaju! Bankomati u Prištini prazni, podignite novac dok ne bude kasno. Niko vam neće reći istinu.",
           "The banks are collapsing! ATMs in Pristina are empty, withdraw your money before it's too late. Nobody will tell you the truth."),
    "sq": ("Bankat po falimentojnë! Bankomatet në Prishtinë janë bosh, tërhiqni paratë para se të jetë vonë. Askush s'do t'ju thotë të vërtetën.",
           "The banks are going bankrupt! ATMs in Pristina are empty, withdraw your money before it's too late. Nobody will tell you the truth."),
    "en": ("The banks are collapsing! ATMs in Pristina are empty, take your money out before it's too late. Nobody will tell you the truth.", ""),
}
campaign_burst("bank_run", TE + 35 * M, RUN_T, 30, 10, reuse=random.sample(network, 40))
cyber_events.append({
    "ext_id": "hold-cyber-5", "kind": "outage", "title": f"Card payment and ATM outage at {BANK}",
    "target": BANK + SYN, "sector": "finance", "claimed_actor": None,
    "source_name": "Homepage + payment status monitor (recorded)" + SYN, "source_type": "media",
    "source_url": "https://example.org/holdout/status/bank", "claimed_at": TE, "first_seen": TE + 6 * M,
    "raw": {"synthetic": True, "domain": "democommerce.example.org", "keywords": ["demo commerce", "bankomat", "kartel"],
            "gt_status": "confirmed", "gt_hybrid": "bank_run",
            "recorded_checks": probes(TE, [(6, "supports", "Card-payment status page reports a full outage"),
                                           (30, "supports", "Card-payment status page still reports an outage")],
                                      "https://democommerce.example.org/status", name="availability")}})

# f) "voter registry leak" that re-posts a 2023 leak -> refuted
TF = START + 44 * H
cyber_post(HACK, TF, "Kemi listën e votuesve të KQZ-së! Së shpejti publikohet. #leak", "We have the CEC voter list! Publishing soon.",
           "sq", "cyber_voter_claim", "telegram", "low")
cyber_events.append({
    "ext_id": "hold-cyber-6", "kind": "data_breach_claim", "title": "Hacktivists claim a voter-registry leak (CEC)",
    "target": "Central Election Commission voter registry", "sector": "government", "claimed_actor": "holdout_hacktivist_grp" + SYN,
    "source_name": "t.me/holdout_hacktivist_grp" + SYN, "source_type": "hacktivist",
    "source_url": "https://example.org/holdout/telegram/3", "claimed_at": TF, "first_seen": TF + 4 * M,
    "raw": {"synthetic": True, "country": "XK", "keywords": ["kqz", "voter", "birač"], "previously_listed_at": "3 March 2023",
            "previous_listing_url": "https://example.org/holdout/leaks/2023-cec", "gt_status": "refuted"}})

# ------------------------------------------------------------------ write
OUT.mkdir(parents=True, exist_ok=True)
posts.sort(key=lambda p: p["ts"])
with open(OUT / "posts.jsonl", "w", encoding="utf-8") as f:
    for p in posts:
        f.write(json.dumps(p, ensure_ascii=False) + "\n")
with open(OUT / "cyber.json", "w", encoding="utf-8") as f:
    json.dump(cyber_events, f, ensure_ascii=False, indent=1)
factchecks = [
    {"id": "hold-fc-1", "source": "HoldCheck (synthetic fact-checker)", "verdict": "False", "date": "2025-04-03",
     "url": "https://example.org/holdout/factcheck/vaccine",
     "claim": "Claim that children in Ferizaj schools were poisoned by a vaccine. "
              "Fëmijët në Ferizaj u helmuan nga vaksina. Deca u Uroševcu otrovana vakcinom."},
    {"id": "hold-fc-2", "source": "HoldCheck (synthetic fact-checker)", "verdict": "False", "date": "2025-10-15",
     "url": "https://example.org/holdout/factcheck/police-north",
     "claim": "Claim that Kosovo Police will withdraw from the north and Serbian police will return. "
              "Policia e Kosovës largohet nga veriu. Kosovska policija napušta sever."},
    {"id": "hold-fc-3", "source": "HoldCheck (synthetic fact-checker)", "verdict": "False", "date": "2024-12-01",
     "url": "https://example.org/holdout/factcheck/remittance-tax",
     "claim": "Claim that the government will tax diaspora remittances by 20%. "
              "Taksë 20% në paratë e diasporës. Porez od 20% na novac iz dijaspore."},
    {"id": "hold-fc-4", "source": "HoldCheck (synthetic fact-checker)", "verdict": "False", "date": "2025-02-10",
     "url": "https://example.org/holdout/factcheck/visa",
     "claim": "Claim that Kosovo will require visas from all Serbian citizens. Kosova vendos viza. Kosovo uvodi vize."},
]
with open(OUT / "factchecks.json", "w", encoding="utf-8") as f:
    json.dump(factchecks, f, ensure_ascii=False, indent=1)

from collections import Counter
print(f"{len(posts)} posts, {len(accounts)} accounts, {len(cyber_events)} cyber claims")
print(Counter(p["gt_narrative"] for p in posts))
