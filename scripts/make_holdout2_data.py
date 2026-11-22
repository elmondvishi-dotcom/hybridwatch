"""Generate HELD-OUT test 2: another unseen synthetic week (new stories, places, claims, outlets).
Run once; do not change thresholds or keyword lists to fit it.

    py scripts/make_holdout2_data.py          -> data/holdout2/{posts.jsonl, cyber.json, factchecks.json}
    py scripts/evaluate_holdout.py --set 2    -> results
"""
from __future__ import annotations

import json
import random
from pathlib import Path

random.seed(4242)
START = 1794700800  # 2026-11-15 00:00 UTC (another new week)
H = 3600.0
DAY = 86400.0
OUT = Path(__file__).resolve().parent.parent / "data" / "holdout2"

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
    "Rrjeti Lajm (holdout2)": ("sq", "credible", 80000),
    "Western Balkans Monitor (holdout2)": ("en", "credible", 100000),
    "Sever Danas (holdout2)": ("sr", "credible", 45000),
    "Zëri i Dardanisë (holdout2)": ("sq", "credible", 65000),
}
LOWCRED_CHANNELS = {"Vërtet Shqip (holdout2)": ("sq", 10000), "Istina Kosova (holdout2)": ("sr", 9000),
                    "Balkan Insider Leaks (holdout2)": ("en", 4000)}
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



# =================================================================== HELD-OUT SCENARIO 2
# A second unseen week, written after HybridWatch was built and after held-out test 1. Run once; no thresholds,
# keyword lists or rules changed for it. Natural mix of threat types, including kinds test 1 showed are weak.
#
#   02-10  heating_pristina    district heating plant failure in Pristina, organic         -> infrastructure, Pristina
#   04-12  landslide_peja      landslide blocks the Rugova road near Peja, organic          -> infrastructure, Peja
#   05-44  sugar_rumour        old debunked rumour: sugar shortage, rationing, organic      -> disinformation (debunk)
#   08-14  power_mitrovica     power cut in Mitrovica, organic                              -> infrastructure, Mitrovica
#   10-18  eulex_spy           CAMPAIGN: "EULEX caught spying, leaving Kosovo" (debunked)    -> disinformation
#   13-17  leposavic_device    explosive device found under a car, defused                  -> security incident, Leposavić
#   15-21  marathon            city marathon, harmless spike                                -> no threat event
#   18-24  kacanik_smuggling   migrant-smuggling group arrested near Kaçanik                -> organized activity, Kaçanik
#   20-26  dam_collapse        CAMPAIGN: "Gazivoda dam about to burst, evacuate" (new, no fact-check) -> disinformation
#   23-27  ferizaj_grenade     hand grenade thrown at a café in Ferizaj                      -> security incident, Ferizaj
#   26-31  lipjan_tractors     farmers block the highway near Lipjan with tractors          -> organized activity, Lipjan
#   28-36  telecom_outage      mobile network outage, organic                               -> infrastructure
#   28-32  telecom_attack      CAMPAIGN distorting the outage: "Serbian cyber attack, hospitals next" -> disinformation + HYBRID
#   31-37  serbia_exercise     Serbia announces army exercise near the administrative line  -> regional
#   34-42  ballots_gjakova     CAMPAIGN: "diaspora ballots destroyed in Gjakova" (debunked)  -> disinformation
#   38-44  macedonia_border    North Macedonia closes a crossing after a border shooting    -> regional
#   all    exam_results (harmless spike), car_prices (harmless, steady), noise
#   CYBER  DDoS on the finance ministry (confirmed), re-posted ransomware listing (refuted), KEV Ivanti (confirmed),
#          mobile outage (confirmed, hybrid), defacement claim on the university site (refuted), police data claim (unverified)

POLICE = make_account("Kosovo Police (holdout2)", START - 3000 * DAY, 150000, "sq")
M = 60.0


def official(author, ts, text, en, narrative, lang="sq"):
    emit(author, ts, text, en, lang, narrative, source_type="official", credibility="credible", cyr_ok=False)


# ------------------------------------------------------------------ 1. heating failure in Pristina
T = START + 2 * H
news("Rrjeti Lajm (holdout2)", T, "Termokosi: defekt në ngrohtoren e Prishtinës, mijëra banesa pa ngrohje deri nesër.",
     "Termokos: fault at the Pristina heating plant, thousands of flats without heating until tomorrow.", "heating_pristina")
news("Western Balkans Monitor (holdout2)", T + 0.5 * H, "Fault at Pristina's district heating plant leaves thousands of homes without heating in freezing weather.",
     "", "heating_pristina")
organic_wave("heating_pristina", T + 0.1 * H, T + 8 * H, 120, [
    [("Pa ngrohje sërish", "Opet bez grejanja", "No heating again"), ("Radiatorët të ftohtë", "Radijatori hladni", "Radiators are cold"),
     ("Termokosi na la pa ngrohje", "Termokos nas ostavio bez grejanja", "Termokos left us without heating")],
    [("në Prishtinë", "u Prištini", "in Pristina"), ("në Dardani", "u Dardaniji", "in Dardania"), ("në Bregun e Diellit", "na Sunčanom bregu", "in Sunny Hill"), ("", "", "")],
    [("Fëmijët po ngrijnë.", "Deca se smrzavaju.", "The kids are freezing."), ("Deri kur?", "Do kada?", "Until when?"), ("", "", "")]])

# ------------------------------------------------------------------ 2. landslide near Peja
T = START + 4 * H
news("Zëri i Dardanisë (holdout2)", T, "Rrëshqitje dheu në rrugën Pejë–Rugovë, qarkullimi i ndërprerë plotësisht.",
     "Landslide on the Peja–Rugova road, traffic completely stopped.", "landslide_peja")
news("Sever Danas (holdout2)", T + 0.7 * H, "Klizište na putu Peć–Rugova, saobraćaj potpuno obustavljen.",
     "Landslide on the Peć–Rugova road, traffic fully suspended.", "landslide_peja")
organic_wave("landslide_peja", T + 0.2 * H, T + 8 * H, 70, [
    [("Rrëshqitje dheu në Rugovë", "Klizište u Rugovi", "Landslide in Rugova"), ("Gurë e dhe mbi rrugë te Peja", "Kamenje i zemlja na putu kod Peći", "Rocks and mud on the road near Peja")],
    [("rruga e mbyllur", "put zatvoren", "road closed"), ("makinat po kthehen", "auta se vraćaju", "cars turning back"), ("", "", "")],
    [("Kini kujdes!", "Pazite!", "Be careful!"), ("Fotot nga vendi", "Slike sa lica mesta", "Photos from the spot"), ("", "", "")]])

# ------------------------------------------------------------------ 3. sugar shortage rumour (old, debunked, organic)
organic_wave("sugar_rumour", START + 5 * H, START + 44 * H, 45, [
    [("Kujdes,", "Pažnja,", "Careful,"), ("Thonë që", "Kažu da", "They say"), ("", "", "")],
    [("do të mbarojë sheqeri në dyqane", "nestaće šećera u prodavnicama", "shops will run out of sugar"),
     ("sheqeri do të racionohet", "šećer će se prodavati na kilo po osobi", "sugar is going to be rationed")],
    [("blini sa më shumë!", "kupujte dok ima!", "stock up now!"), ("A është e vërtetë?", "Je li to istina?", "Is it true?"), ("", "", "")]],
    shape="flat", debunk="hold2-fc-3")

# ------------------------------------------------------------------ 4. power cut in Mitrovica
T = START + 8 * H
news("Sever Danas (holdout2)", T, "Nestanak struje u severnom delu Mitrovice zbog kvara na dalekovodu.",
     "Power cut in the northern part of Mitrovica due to a fault on a power line.", "power_mitrovica")
news("Rrjeti Lajm (holdout2)", T + 0.5 * H, "Ndërprerje e rrymës në Mitrovicë për shkak të defektit në largpërçues.",
     "Power cut in Mitrovica due to a fault on a transmission line.", "power_mitrovica")
organic_wave("power_mitrovica", T + 0.1 * H, T + 5 * H, 80, [
    [("Nuk ka rrymë në Mitrovicë", "Nema struje u Mitrovici", "No power in Mitrovica"), ("Na iku drita", "Nestalo struje", "The power went out")],
    [("prej dy orësh", "već dva sata", "for two hours"), ("në veri", "na severu", "in the north"), ("", "", "")],
    [("Kur rregullohet?", "Kad će popraviti?", "When will it be fixed?"), ("", "", "")]])

# ------------------------------------------------------------------ 5. EULEX spying CAMPAIGN (debunked)
T = START + 10 * H
emit("Istina Kosova (holdout2)", T - 0.4 * H, "EKSKLUZIVNO: EULEX uhvaćen u špijunaži, misija napušta Kosovo do kraja meseca!",
     "EXCLUSIVE: EULEX caught spying, the mission leaves Kosovo by the end of the month!", "sr", "eulex_spy",
     source_type="telegram", credibility="low", debunk="hold2-fc-1")
EULEX_T = {
    "sr": ("EULEX uhvaćen u špijunaži za stranu službu, misija napušta Kosovo do kraja meseca! Mediji ćute, podelite.",
           "EULEX caught spying for a foreign service, the mission leaves Kosovo by the end of the month! Media are silent, share."),
    "sq": ("EULEX u kap duke spiunuar për një shërbim të huaj, misioni largohet nga Kosova deri në fund të muajit! Shpërndajeni.",
           "EULEX caught spying for a foreign service, the mission leaves Kosovo by the end of the month! Share it."),
    "en": ("EULEX caught spying for a foreign service, mission leaving Kosovo by the end of the month! The media won't tell you, share.", ""),
}
ewave = campaign_burst("eulex_spy", T, EULEX_T, 40, 12, debunk="hold2-fc-1")
organic_wave("eulex_spy", T + 0.4 * H, T + 7 * H, 45, [
    [("A është e vërtetë që EULEX po spiunonte?", "Da li je tačno da je EULEX špijunirao?", "Is it true EULEX was spying?"),
     ("Lexova që EULEX largohet", "Pročitao sam da EULEX odlazi", "I read EULEX is leaving")],
    [("Burim?", "Izvor?", "Source?"), ("Kjo duket e rreme.", "Ovo izgleda lažno.", "This looks fake."), ("", "", "")]], debunk="hold2-fc-1")
news("Western Balkans Monitor (holdout2)", T + 3 * H, "EULEX rejects claims of espionage and says the mission is not leaving Kosovo.", "", "eulex_spy")

# ------------------------------------------------------------------ 6. explosive device in Leposavić
T = START + 13 * H
official(POLICE, T, "Policia gjeti një mjet shpërthyes nën një veturë në Leposaviq. Ekspertët e çaktivizuan, nuk ka të lënduar.",
         "Police found an explosive device under a car in Leposavić. Experts defused it, no one was hurt.", "leposavic_device")
news("Sever Danas (holdout2)", T + 0.5 * H, "Eksplozivna naprava pronađena ispod automobila u Leposaviću, deaktivirana.",
     "Explosive device found under a car in Leposavić, defused.", "leposavic_device")
news("Western Balkans Monitor (holdout2)", T + 0.8 * H, "Explosive device found under a car in Leposavić in northern Kosovo; police defuse it.", "",
     "leposavic_device")
organic_wave("leposavic_device", T + 0.3 * H, T + 5 * H, 35, [
    [("Mjet shpërthyes nën veturë në Leposaviq", "Bomba ispod auta u Leposaviću", "Bomb under a car in Leposavić")],
    [("u çaktivizua", "deaktivirana", "defused"), ("policia në vend", "policija na licu mesta", "police at the scene"), ("", "", "")],
    [("Sërish tension në veri.", "Opet napetost na severu.", "Tension in the north again."), ("", "", "")]])

# ------------------------------------------------------------------ 7. marathon (harmless spike)
organic_wave("marathon", START + 15 * H, START + 21 * H, 200, [
    [("Maratona e Prishtinës", "Prištinski maraton", "Pristina marathon"), ("Gara e sotme", "Današnja trka", "Today's race")],
    [("mijëra vrapues", "hiljade trkača", "thousands of runners"), ("rekord i ri", "novi rekord", "a new record"), ("e përfundova!", "završio sam!", "I finished!")],
    [("🏃", "🏃", "🏃"), ("Bravo të gjithëve!", "Bravo svima!", "Well done everyone!"), ("", "", "")]])

# ------------------------------------------------------------------ 8. migrant smuggling near Kaçanik
T = START + 18 * H
official(POLICE, T, "Policia arrestoi gjashtë persona për kontrabandë me migrantë afër Kaçanikut. U gjetën 23 migrantë në një kamion.",
         "Police arrested six people for migrant smuggling near Kaçanik. 23 migrants were found in a truck.", "kacanik_smuggling")
news("Zëri i Dardanisë (holdout2)", T + 0.6 * H, "Arrestohen gjashtë kontrabandistë me migrantë te Kaçaniku.",
     "Six migrant smugglers arrested near Kaçanik.", "kacanik_smuggling")
news("Western Balkans Monitor (holdout2)", T + 1 * H, "Kosovo police break up a migrant-smuggling ring near Kaçanik, six arrested.", "", "kacanik_smuggling")
organic_wave("kacanik_smuggling", T + 0.7 * H, T + 5 * H, 12, [
    [("Kontrabandë me migrantë te Kaçaniku", "Krijumčarenje migranata kod Kačanika", "Migrant smuggling near Kaçanik")],
    [("gjashtë të arrestuar", "šestorica uhapšena", "six arrested"), ("23 migrantë në kamion", "23 migranta u kamionu", "23 migrants in a truck")]])

# ------------------------------------------------------------------ 9. Gazivoda dam CAMPAIGN (new claim, no fact-check)
T = START + 20 * H
DAM_T = {
    "sr": ("HITNO: brana Gazivode samo što nije pukla, evakuišite Zubin Potok i Mitrovicu odmah! Vlasti ćute.",
           "URGENT: the Gazivoda dam is about to burst, evacuate Zubin Potok and Mitrovica now! The authorities are silent."),
    "sq": ("URGJENT: diga e Ujmanit po shembet, evakuoni Zubin Potokun dhe Mitrovicën menjëherë! Autoritetet heshtin.",
           "URGENT: the Ujman dam is collapsing, evacuate Zubin Potok and Mitrovica immediately! The authorities are silent."),
    "en": ("URGENT: Gazivoda dam about to burst, evacuate Zubin Potok and Mitrovica now! Authorities are hiding it.", ""),
}
campaign_burst("dam_collapse", T, DAM_T, 34, 10, reuse=random.sample(ewave, 25) + random.sample([a for a in network if a not in ewave], 15))
organic_wave("dam_collapse", T + 0.3 * H, T + 5 * H, 40, [
    [("A po shembet diga e Ujmanit?", "Da li puca brana Gazivode?", "Is the Gazivoda dam bursting?"), ("Paniku për digën", "Panika zbog brane", "Panic about the dam")],
    [("Kush e di diçka?", "Zna li neko nešto?", "Anyone know anything?"), ("Po ikim nga shtëpia.", "Napuštamo kuću.", "We're leaving the house."), ("", "", "")]])
news("Rrjeti Lajm (holdout2)", T + 2.5 * H, "Ujmani: diga është e sigurt, lajmet për shembje janë të rreme.",
     "Ibar-Lepenac company: the dam is safe, reports of a collapse are false.", "dam_collapse")

# ------------------------------------------------------------------ 10. grenade at a café in Ferizaj
T = START + 23 * H
news("Zëri i Dardanisë (holdout2)", T + 15 * M, "Granatë dore e hedhur në një kafene në Ferizaj natën, dëme materiale, nuk ka të plagosur.",
     "Hand grenade thrown at a café in Ferizaj at night, material damage, no one wounded.", "ferizaj_grenade")
official(POLICE, T + 40 * M, "Policia heton hedhjen e një granate dore në një lokal në Ferizaj. Nuk ka të lënduar.",
         "Police are investigating a hand grenade thrown at a bar in Ferizaj. No one was hurt.", "ferizaj_grenade")
organic_wave("ferizaj_grenade", T + 10 * M, T + 5 * H, 35, [
    [("Granatë në një kafene në Ferizaj", "Bomba bačena na kafić u Uroševcu", "Grenade thrown at a café in Ferizaj"), ("Shpërthim natën në Ferizaj", "Eksplozija noću u Uroševcu", "Explosion at night in Ferizaj")],
    [("nuk ka të plagosur", "nema povređenih", "no one hurt"), ("xhamat u thyen", "razbijena stakla", "windows smashed"), ("", "", "")]])

# ------------------------------------------------------------------ 11. tractors block the highway near Lipjan
T = START + 26 * H
news("Rrjeti Lajm (holdout2)", T + 0.3 * H, "Fermerët bllokuan autostradën afër Lipjanit me traktorë, kërkojnë subvencione.",
     "Farmers blocked the highway near Lipjan with tractors, demanding subsidies.", "lipjan_tractors")
news("Western Balkans Monitor (holdout2)", T + 0.8 * H, "Farmers block the highway near Lipjan with tractors in a protest over subsidies.", "", "lipjan_tractors")
organic_wave("lipjan_tractors", T, T + 5 * H, 50, [
    [("Fermerët me traktorë në autostradë te Lipjani", "Poljoprivrednici traktorima blokirali autoput kod Lipljana", "Farmers with tractors on the highway near Lipjan")],
    [("protestë për subvencione", "protest zbog subvencija", "protest over subsidies"), ("trafiku i bllokuar", "saobraćaj blokiran", "traffic blocked"), ("", "", "")],
    [("I mbështes!", "Podržavam ih!", "I support them!"), ("", "", "")]])

# ------------------------------------------------------------------ 12. mobile network outage + "Serbian cyber attack" CAMPAIGN (hybrid)
TE = START + 28 * H
TEL = "Demo Mobile Kosovo"
news("Zëri i Dardanisë (holdout2)", TE + 20 * M, f"{TEL}: rrjeti mobil dhe interneti nuk funksionojnë në disa qytete.",
     f"{TEL}: the mobile network and internet are down in several cities.", "telecom_outage")
official(make_account(f"{TEL} (holdout2)", START - 3000 * DAY, 60000, "sq"), TE + 45 * M,
         "Kemi një problem teknik në rrjet. Ekipet po punojnë për rikthimin e shërbimit.",
         "We have a technical problem in the network. Teams are working to restore the service.", "telecom_outage")
organic_wave("telecom_outage", TE + 5 * M, TE + 6 * H, 90, [
    [("Nuk kam sinjal", "Nemam signal", "No signal"), ("Interneti në telefon nuk punon", "Ne radi internet na telefonu", "Mobile internet isn't working"),
     ("Rrjeti mobil ra", "Pala mobilna mreža", "The mobile network is down")],
    [("në Prishtinë", "u Prištini", "in Pristina"), ("në Prizren", "u Prizrenu", "in Prizren"), ("", "", "")],
    [("Çka po ndodh?", "Šta se dešava?", "What's going on?"), ("", "", "")]])
TA_T = {
    "sr": ("Pad mreže nije kvar, to je srpski sajber napad! Sledeće na meti su bolnice, spremite se.",
           "The network outage is not a fault, it is a Serbian cyber attack! Hospitals are the next target, get ready."),
    "sq": ("Rënia e rrjetit nuk është defekt, është sulm kibernetik serb! Objektivi i radhës janë spitalet, bëhuni gati.",
           "The network crash is not a fault, it is a Serbian cyber attack! Hospitals are the next target, get ready."),
    "en": ("The mobile network crash was no accident, it is a Serbian cyber attack! Hospitals are next, be ready.", ""),
}
campaign_burst("telecom_attack", TE + 30 * M, TA_T, 30, 10, reuse=random.sample(network, 40))

# ------------------------------------------------------------------ 13. Serbia army exercise (regional)
T = START + 31 * H
news("Sever Danas (holdout2)", T, "Vojska Srbije najavila veliku vežbu u blizini administrativne linije sa Kosovom.",
     "The Serbian army announced a large exercise near the administrative line with Kosovo.", "serbia_exercise")
news("Western Balkans Monitor (holdout2)", T + 0.5 * H, "Serbia announces a large army exercise near the line with Kosovo; KFOR says it is monitoring.", "", "serbia_exercise")
news("Rrjeti Lajm (holdout2)", T + 1 * H, "Ushtria e Serbisë njofton stërvitje të madhe pranë vijës me Kosovën.",
     "The Serbian army announces a large exercise near the line with Kosovo.", "serbia_exercise")
organic_wave("serbia_exercise", T + 0.2 * H, T + 6 * H, 35, [
    [("Stërvitje e ushtrisë serbe pranë kufirit", "Vežba vojske Srbije kod administrativne linije", "Serbian army exercise near the line")],
    [("KFOR po monitoron", "KFOR prati situaciju", "KFOR is monitoring"), ("tanke dhe ushtarë", "tenkovi i vojnici", "tanks and soldiers"), ("", "", "")],
    [("Shqetësuese.", "Zabrinjavajuće.", "Worrying."), ("", "", "")]])

# ------------------------------------------------------------------ 14. "destroyed diaspora ballots" CAMPAIGN (debunked)
T = START + 34 * H
BAL_T = {
    "sq": ("SKANDAL: votat e diasporës u shkatërruan në Gjakovë, zgjedhjet janë manipuluar! KQZ e fsheh, shpërndajeni.",
           "SCANDAL: the diaspora votes were destroyed in Gjakova, the elections are rigged! The CEC hides it, share."),
    "sr": ("SKANDAL: glasovi dijaspore uništeni u Đakovici, izbori su namešteni! CIK to krije, podelite.",
           "SCANDAL: diaspora votes destroyed in Gjakova, the elections are rigged! The CEC hides it, share."),
    "en": ("SCANDAL: diaspora ballots destroyed in Gjakova, the election is rigged! The CEC is hiding it, share.", ""),
}
campaign_burst("ballots_gjakova", T, BAL_T, 38, 14, debunk="hold2-fc-2", reuse=random.sample(network, 45))
organic_wave("ballots_gjakova", T + 0.5 * H, T + 7 * H, 50, [
    [("A u shkatërruan votat e diasporës në Gjakovë?", "Da li su uništeni glasovi dijaspore u Đakovici?", "Were the diaspora votes destroyed in Gjakova?")],
    [("Kjo është e rëndë nëse është e vërtetë.", "Ovo je ozbiljno ako je istina.", "Serious if true."), ("Burim?", "Izvor?", "Source?"), ("", "", "")]], debunk="hold2-fc-2")
news("Zëri i Dardanisë (holdout2)", T + 3 * H, "KQZ: lajmet për shkatërrimin e votave të diasporës në Gjakovë janë të rreme.",
     "CEC: reports that diaspora ballots were destroyed in Gjakova are false.", "ballots_gjakova")

# ------------------------------------------------------------------ 15. North Macedonia closes a crossing (regional)
T = START + 38 * H
news("Western Balkans Monitor (holdout2)", T, "North Macedonia closes the Blace border crossing with Kosovo after a shooting near the border.", "", "macedonia_border")
news("Rrjeti Lajm (holdout2)", T + 0.6 * H, "Maqedonia e Veriut mbyll pikëkalimin e Bllacës me Kosovën pas të shtënave pranë kufirit.",
     "North Macedonia closes the Blace crossing with Kosovo after shots near the border.", "macedonia_border")
organic_wave("macedonia_border", T + 0.3 * H, T + 6 * H, 30, [
    [("U mbyll kufiri me Maqedoninë", "Zatvorena granica sa Makedonijom", "Border with North Macedonia closed"), ("Bllaca e mbyllur", "Blace zatvoreno", "Blace crossing closed")],
    [("pas të shtënave", "posle pucnjave", "after a shooting"), ("kamionët në radhë", "kamioni u redu", "trucks queuing"), ("", "", "")]])

# ------------------------------------------------------------------ 16. harmless: exam results spike, car prices steady
organic_wave("exam_results", START + 40 * H, START + 45 * H, 160, [
    [("Dolën rezultatet e testit të maturës", "Izašli rezultati mature", "The school-leaving exam results are out")],
    [("kalova!", "prošao sam!", "I passed!"), ("pikët e mia", "moji poeni", "my points"), ("pres universitetin", "čekam fakultet", "waiting for university")],
    [("🎓", "🎓", "🎓"), ("", "", "")]])
organic_wave("car_prices", START, START + 48 * H, 90, [
    [("Çmimet e veturave", "Cene automobila", "Car prices"), ("Doganat për vetura", "Carina za automobile", "Customs on cars")],
    [("janë rritur shumë", "su mnogo porasle", "have gone up a lot"), ("në Gjermani janë më lirë", "u Nemačkoj su jeftiniji", "are cheaper in Germany")],
    [("", "", ""), ("Si ia bëni?", "Kako vi?", "How do you manage?")]], shape="flat")

# ------------------------------------------------------------------ 17. noise
NOISE_SQ = ["Kafe e mëngjesit", "Filmi i sontëm", "Ndeshja e basketbollit", "Pica me shokët", "Shëtitje në park", "Libri i ri i Kadaresë",
            "Koncert i mirë", "Intervistë pune nesër", "Tavë kosi sot", "Kufjet e reja", "Vrapim në mëngjes", "Dasma e kushëririt"]
NOISE_SR = ["Jutarnja kafa", "Film večeras", "Košarkaška utakmica", "Pica sa društvom", "Šetnja u parku", "Nova knjiga",
            "Dobar koncert", "Razgovor za posao sutra", "Pasulj danas", "Nove slušalice", "Jutarnje trčanje", "Venčanje rođaka"]
NOISE_EN = ["Morning coffee", "Film tonight", "Basketball game", "Pizza with friends", "Walk in the park", "New book",
            "Good concert", "Job interview tomorrow", "Home cooking today", "New headphones", "Morning run", "Cousin's wedding"]
for _ in range(1500):
    lang = random.choices(["sq", "sr", "en"], [0.45, 0.4, 0.15])[0]
    base = {"sq": NOISE_SQ, "sr": NOISE_SR, "en": NOISE_EN}[lang]
    i = random.randrange(len(base))
    tail = random.choice(["", "", " :)", " 😊", f" #{random.randint(1, 999)}", " !"])
    emit(pick(organic[lang]), random.uniform(START, START + 48 * H), base[i] + tail, NOISE_EN[i] + tail, lang, "noise",
         source_type=pick(["x", "facebook", "instagram"]))

# ------------------------------------------------------------------ 18. CYBER (recorded checks)
HACK = make_account("holdout2_hacktivist", START - 15 * DAY, 2700, "mix")
SYN = " (synthetic)"
cyber_events = []


def cyber_post(author, ts, text, en, lang, narrative, source_type, credibility):
    emit(author, ts, text, en, lang, narrative, source_type=source_type, credibility=credibility, cyr_ok=False)


def probes(t0, pattern, url, name="availability"):
    return [{"check_name": name, "result": r, "detail": d, "evidence_url": url, "checked_at": round(t0 + m * M, 1)} for m, r, d in pattern]


# a) DDoS on the finance ministry portal -> confirmed
TA = START + 9 * H
cyber_post(HACK, TA, "E rrëzuam faqen e Ministrisë së Financave! #DDoS", "We took down the Ministry of Finance website! #DDoS", "sq", "cy2_finance", "telegram", "low")
cyber_post("Rrjeti Lajm (holdout2)", TA + 55 * M, "Faqja e Ministrisë së Financave ishte jashtë funksionit rreth një orë pas një sulmi DDoS.",
           "The Ministry of Finance website was down for about an hour after a DDoS attack.", "sq", "cy2_finance", "news", "credible")
cyber_post(make_account("Ministry of Finance (holdout2)", START - 3000 * DAY, 20000, "sq"), TA + 85 * M,
           "Faqja e ministrisë u godit nga një sulm DDoS dhe është rikthyer. Nuk ka rrjedhje të të dhënave.",
           "The ministry website was hit by a DDoS attack and is back. No data was leaked.", "sq", "cy2_finance", "official", "credible")
cyber_events.append({
    "ext_id": "hold2-cyber-1", "kind": "ddos_claim", "title": "Hacktivists claim DDoS on the Ministry of Finance website",
    "target": "Ministry of Finance website", "sector": "government", "claimed_actor": "holdout2_hacktivist" + SYN,
    "source_name": "t.me/holdout2_hacktivist" + SYN, "source_type": "hacktivist", "source_url": "https://example.org/holdout2/telegram/1",
    "claimed_at": TA, "first_seen": TA + 2 * M,
    "raw": {"synthetic": True, "domain": "mf.example.org", "keywords": ["ministria e financave", "ministry of finance", "ministarstvo finansija"],
            "gt_status": "confirmed",
            "recorded_checks": probes(TA, [(4, "supports", "Homepage not reachable (timeout after 10 s)"), (20, "supports", "Homepage not reachable (HTTP 503)"),
                                           (55, "contradicts", "Homepage up again (HTTP 200): about an hour of downtime")], "https://mf.example.org/")}})

# b) ransomware listing that re-posts a 2024 victim -> refuted
TB = START + 16 * H
cyber_events.append({
    "ext_id": "hold2-cyber-2", "kind": "ransomware_claim", "title": "Ransomware listing: Demo Water Company, Gjakova",
    "target": "Demo Water Company Gjakova" + SYN, "sector": "other", "claimed_actor": "DemoLock" + SYN,
    "source_name": "Ransomware tracker (recorded)" + SYN, "source_type": "ransomware_tracker",
    "source_url": "https://example.org/holdout2/ransomware-tracker/water", "claimed_at": TB, "first_seen": TB + 6 * M,
    "raw": {"synthetic": True, "country": "XK", "description": "Regional water company in Gjakova, Kosovo",
            "previously_listed_at": "9 August 2024", "previous_listing_url": "https://example.org/holdout2/ransomware-tracker/water-2024",
            "gt_status": "refuted"}})

# c) actively exploited Ivanti flaw -> confirmed
TC = START + 22 * H
cyber_events.append({
    "ext_id": "hold2-cyber-3", "kind": "vulnerability", "title": "CVE-2024-21887 (Ivanti Connect Secure) actively exploited",
    "target": "Ivanti Connect Secure", "sector": "other", "claimed_actor": None,
    "source_name": "CISA KEV (recorded)" + SYN, "source_type": "kev", "source_url": "https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
    "claimed_at": TC, "first_seen": TC,
    "raw": {"synthetic": True, "cve": "CVE-2024-21887", "vendor": "Ivanti", "product": "Connect Secure", "gt_status": "confirmed",
            "recorded_checks": [{"check_name": "kev", "result": "supports", "checked_at": round(TC, 1),
                                 "detail": "CVE-2024-21887 (Ivanti Connect Secure command injection) listed as actively exploited",
                                 "evidence_url": "https://nvd.nist.gov/vuln/detail/CVE-2024-21887"}]}})

# d) mobile network outage -> confirmed, hybrid with telecom_attack
cyber_events.append({
    "ext_id": "hold2-cyber-4", "kind": "outage", "title": f"Mobile network and internet outage at {TEL}",
    "target": TEL + SYN, "sector": "telecom", "claimed_actor": None,
    "source_name": "IODA (recorded)" + SYN, "source_type": "ioda", "source_url": "https://ioda.inetintel.cc.gatech.edu/country/XK",
    "claimed_at": TE, "first_seen": TE + 7 * M,
    "raw": {"synthetic": True, "asn": "AS0001", "keywords": ["demo mobile", "rrjeti mobil", "mobilna mreža", "internet"],
            "gt_status": "confirmed", "gt_hybrid": "telecom_attack",
            "recorded_checks": [{"check_name": "ioda", "result": "supports", "checked_at": round(TE + 7 * M, 1),
                                 "detail": "Active-probing signal for AS0001 dropped 64%, BGP visibility -40%",
                                 "evidence_url": "https://ioda.inetintel.cc.gatech.edu/asn/1"}]}})

# e) defacement claim on the university website, page unchanged -> refuted
TF = START + 33 * H
cyber_post(HACK, TF, "Faqja e Universitetit të Prishtinës u hakua nga ne! #defaced", "We hacked the University of Pristina website!", "sq", "cy2_uni", "telegram", "low")
cyber_events.append({
    "ext_id": "hold2-cyber-5", "kind": "defacement", "title": "Hacktivists claim defacement of the University of Pristina website",
    "target": "University of Pristina website", "sector": "government", "claimed_actor": "holdout2_hacktivist" + SYN,
    "source_name": "t.me/holdout2_hacktivist" + SYN, "source_type": "hacktivist", "source_url": "https://example.org/holdout2/telegram/2",
    "claimed_at": TF, "first_seen": TF + 3 * M,
    "raw": {"synthetic": True, "domain": "uni.example.org", "keywords": ["universitet", "univerzitet", "university"], "gt_status": "refuted",
            "recorded_checks": probes(TF, [(5, "contradicts", "Homepage up (HTTP 200, 0.4 s)"), (25, "contradicts", "Homepage up (HTTP 200, 0.5 s)")], "https://uni.example.org/")
            + probes(TF, [(5, "contradicts", "Homepage content unchanged since the last check"), (25, "contradicts", "Homepage content unchanged")],
                     "https://uni.example.org/", name="content_hash")}})

# f) claim of a police personnel-data leak, nothing supports it -> unverified
TG = START + 41 * H
cyber_post(HACK, TG, "Kemi të dhënat e punonjësve të Policisë së Kosovës. Së shpejti. #leak", "We have Kosovo Police staff data. Soon.", "sq", "cy2_police", "telegram", "low")
cyber_events.append({
    "ext_id": "hold2-cyber-6", "kind": "data_breach_claim", "title": "Hacktivists claim a leak of Kosovo Police staff data",
    "target": "Kosovo Police staff data", "sector": "government", "claimed_actor": "holdout2_hacktivist" + SYN,
    "source_name": "t.me/holdout2_hacktivist" + SYN, "source_type": "hacktivist", "source_url": "https://example.org/holdout2/telegram/3",
    "claimed_at": TG, "first_seen": TG + 4 * M,
    "raw": {"synthetic": True, "country": "XK", "keywords": ["policia e kosovës", "kosovo police"], "gt_status": "unverified"}})

# ------------------------------------------------------------------ write
OUT.mkdir(parents=True, exist_ok=True)
posts.sort(key=lambda p: p["ts"])
with open(OUT / "posts.jsonl", "w", encoding="utf-8") as f:
    for p in posts:
        f.write(json.dumps(p, ensure_ascii=False) + "\n")
with open(OUT / "cyber.json", "w", encoding="utf-8") as f:
    json.dump(cyber_events, f, ensure_ascii=False, indent=1)
factchecks = [
    {"id": "hold2-fc-1", "source": "HoldCheck (synthetic fact-checker)", "verdict": "False", "date": "2025-05-20",
     "url": "https://example.org/holdout2/factcheck/eulex",
     "claim": "Claim that EULEX was caught spying and is leaving Kosovo. EULEX u kap duke spiunuar. EULEX uhvaćen u špijunaži."},
    {"id": "hold2-fc-2", "source": "HoldCheck (synthetic fact-checker)", "verdict": "False", "date": "2025-02-14",
     "url": "https://example.org/holdout2/factcheck/ballots",
     "claim": "Claim that diaspora ballots were destroyed in Gjakova and the election was rigged. "
              "Votat e diasporës u shkatërruan në Gjakovë. Glasovi dijaspore uništeni u Đakovici."},
    {"id": "hold2-fc-3", "source": "HoldCheck (synthetic fact-checker)", "verdict": "False", "date": "2024-10-03",
     "url": "https://example.org/holdout2/factcheck/sugar",
     "claim": "Claim that shops will run out of sugar and sugar will be rationed. Do të mbarojë sheqeri. Nestaće šećera."},
    {"id": "hold2-fc-4", "source": "HoldCheck (synthetic fact-checker)", "verdict": "Misleading", "date": "2025-07-01",
     "url": "https://example.org/holdout2/factcheck/fuel",
     "claim": "Claim that fuel will cost 3 euros per litre next month. Nafta 3 euro. Gorivo 3 evra."},
]
with open(OUT / "factchecks.json", "w", encoding="utf-8") as f:
    json.dump(factchecks, f, ensure_ascii=False, indent=1)

from collections import Counter
print(f"{len(posts)} posts, {len(accounts)} accounts, {len(cyber_events)} cyber claims")
print(Counter(p["gt_narrative"] for p in posts))
