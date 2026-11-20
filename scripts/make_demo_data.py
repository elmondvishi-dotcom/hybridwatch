"""Generate a SYNTHETIC, labelled 48-hour replay dataset for demos and evaluation.

All outlets, accounts, events and fact-checks are fictional. Every post carries
ground-truth labels (gt_narrative, gt_coordinated, gt_debunk) so scripts/evaluate.py
can measure how well the pipeline does. Replace with a real recorded dataset
(same JSONL format) for the hackathon replay of a real event.

Scenario (hours from start):
  02-08  power_outage    real outage in Pristina, credible news first, organic
  06-20  snow_roads      real snowstorm closes roads, organic, fast growth -> 'trending'
  08-44  euro_freeze     old debunked rumour resurfacing organically -> 'debunk_match'
  14-22  water_poison    CAMPAIGN: copy-paste burst by a network of new accounts, recycled debunked claim
  20-24  football        match night, organic spike -> 'trending', not relevant
  30-38  kfor_withdraw   CAMPAIGN: same network as water_poison (actor overlap)
  36-42  power_sabotage  CAMPAIGN: distorts the earlier real outage ("it was sabotage")
  all    energy_prices, noise
"""
from __future__ import annotations

import json
import random
from pathlib import Path

random.seed(42)
START = 1793491200  # 2026-11-01 00:00 UTC
H = 3600.0
DAY = 86400.0
OUT = Path(__file__).resolve().parent.parent / "data"

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
    "Prishtina Daily (demo)": ("sq", "credible", 85000),
    "Balkan Wire (demo)": ("en", "credible", 120000),
    "Novi Dnevnik (demo)": ("sr", "credible", 95000),
    "Kosova Times (demo)": ("sq", "credible", 60000),
}
LOWCRED_CHANNELS = {"Kanali Info (demo)": ("sq", 9000), "Istina Uživo (demo)": ("sr", 14000),
                    "Breaking Balkans (demo)": ("en", 6000)}
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


# ------------------------------------------------------------------ 1. power outage (organic)
T = START + 2 * H
news("Prishtina Daily (demo)", T, "Ndërprerje e energjisë elektrike në disa lagje të Prishtinës për shkak të defektit në trafo.",
     "Power cut in several Pristina neighbourhoods due to a transformer fault.", "power_outage")
news("Balkan Wire (demo)", T + 0.3 * H, "Power outage hits parts of Pristina after transformer fault, utility says repairs under way.",
     "", "power_outage")
OUT_PARTS = [
    [("Pa rrymë", "Nema struje", "No power"), ("Na ra rryma", "Nestalo struje", "Power just went out"),
     ("Përsëri pa energji elektrike", "Opet bez struje", "Again without electricity")],
    [("në lagjen tonë në Prishtinë", "u našem delu Prištine", "in our part of Pristina"),
     ("në Ulpianë", "na Ulpijani", "in Ulpiana"), ("në qendër të Prishtinës", "u centru Prištine", "in central Pristina"),
     ("në Dardani", "u Dardaniji", "in Dardania")],
    [("që nga ora 2.", "od 2 sata.", "since 2am."), ("prej një ore.", "već sat vremena.", "for an hour now."),
     ("edhe sot.", "i danas.", "again today."), ("", "", "")],
    [("Kur kthehet?", "Kada će vratiti?", "When is it coming back?"),
     ("KEDS thotë që po e rregullojnë.", "Kažu da popravljaju trafo.", "They say the transformer is being fixed."),
     ("Ftohtë shumë pa ngrohje.", "Hladno je bez grejanja.", "Very cold without heating."), ("", "", "")],
]
organic_wave("power_outage", T + 0.1 * H, T + 6 * H, 130, OUT_PARTS)

# ------------------------------------------------------------------ 2. snowstorm (organic, trending)
T = START + 6 * H
news("Kosova Times (demo)", T, "Reshjet e dendura të borës bllokojnë disa rrugë kryesore, autoritetet bëjnë thirrje për kujdes.",
     "Heavy snowfall blocks several main roads, authorities urge caution.", "snow_roads")
news("Novi Dnevnik (demo)", T + 0.4 * H, "Obilan sneg zatvorio više puteva na jugu Srbije i Kosovu, vozači da budu oprezni.",
     "Heavy snow closed several roads in southern Serbia and Kosovo, drivers urged to be careful.", "snow_roads")
SNOW_PARTS = [
    [("Bora e madhe", "Ogroman sneg", "Huge snowfall"), ("Shumë borë sot", "Puno snega danas", "So much snow today"),
     ("Stuhi bore", "Snežna oluja", "Snowstorm"), ("Ka rënë borë e dendur", "Pada gust sneg", "Heavy snow falling")],
    [("rruga për Pejë është e mbyllur", "put prema Peći je zatvoren", "the road to Peja is closed"),
     ("autostrada e bllokuar", "autoput blokiran", "the highway is blocked"),
     ("rrugët në Mitrovicë të pakalueshme", "putevi u Mitrovici neprohodni", "roads in Mitrovica impassable"),
     ("kamionë të bllokuar te Merdari", "kamioni zaglavljeni kod Merdara", "trucks stuck at Merdare"),
     ("shkollat mbyllen nesër", "škole zatvorene sutra", "schools closed tomorrow")],
    [("Kini kujdes në rrugë!", "Vozite pažljivo!", "Drive carefully!"), ("Mos udhëtoni sot.", "Ne putujte danas.", "Don't travel today."),
     ("Fotot nga sot", "Slike od danas", "Pictures from today"), ("", "", ""), ("", "", "")],
]
organic_wave("snow_roads", T + 0.2 * H, T + 14 * H, 320, SNOW_PARTS)

# ------------------------------------------------------------------ 3. euro freeze (old debunked rumour, organic)
EURO_PARTS = [
    [("A e dini që", "Da li ste čuli da", "Did you hear that"), ("Thonë që", "Kažu da", "People say"),
     ("Kujdes, po thuhet që", "Pažnja, priča se da", "Careful, word is that"), ("", "", "")],
    [("bankat do t'i bllokojnë llogaritë në euro", "banke će zamrznuti račune u evrima",
      "banks will freeze euro accounts"),
     ("bankat po i ngrijnë kursimet në euro", "banke blokiraju evro štednju", "banks are freezing euro savings"),
     ("do të bllokohen depozitat në euro në banka", "evro depoziti u bankama biće blokirani",
      "euro deposits in banks will be blocked")],
    [("javën e ardhshme", "od sledeće nedelje", "from next week"), ("nga muaji tjetër", "od idućeg meseca", "from next month"),
     ("së shpejti", "uskoro", "soon")],
    [("Tërhiqni paratë!", "Podignite novac!", "Withdraw your money!"), ("A është e vërtetë?", "Je li to tačno?", "Is it true?"),
     ("Kush di më shumë?", "Ko zna nešto više?", "Anyone know more?"), ("", "", "")],
]
organic_wave("euro_freeze", START + 8 * H, START + 44 * H, 45, EURO_PARTS, shape="flat", debunk="demo-fc-3")

# ------------------------------------------------------------------ 4. water poison CAMPAIGN (recycled debunked claim)
T = START + 14 * H
emit("Istina Uživo (demo)", T - 0.4 * H, "HITNO: voda u Mitrovici zatrovana, ne pijte vodu iz česme! Vlasti kriju istinu.",
     "URGENT: water in Mitrovica poisoned, do not drink tap water! Authorities are hiding the truth.", "sr",
     "water_poison", source_type="telegram", credibility="low", debunk="demo-fc-1")
emit("Kanali Info (demo)", T - 0.3 * H, "URGJENT: uji në Mitrovicë është helmuar, mos pini ujë nga rubineti! Autoritetet po e fshehin.",
     "URGENT: the water in Mitrovica has been poisoned, do not drink tap water! Authorities are hiding it.", "sq",
     "water_poison", source_type="telegram", credibility="low", debunk="demo-fc-1")
WATER_T = {
    "sr": ("HITNO: voda u Mitrovici je zatrovana, ne pijte vodu iz česme! Vlasti kriju istinu, podelite sa svima.",
           "URGENT: the water in Mitrovica is poisoned, don't drink tap water! Authorities hide the truth, share with everyone."),
    "sq": ("URGJENT: uji në Mitrovicë është helmuar, mos pini ujë nga rubineti! Autoritetet e fshehin të vërtetën, shpërndajeni.",
           "URGENT: the water in Mitrovica has been poisoned, don't drink tap water! Authorities hide the truth, share it."),
    "en": ("URGENT: Mitrovica water supply has been poisoned, do not drink tap water! Authorities are hiding the truth, share now.", ""),
}
wave1 = campaign_burst("water_poison", T, WATER_T, 46, 12, debunk="demo-fc-1")
campaign_burst("water_poison", T + 1.5 * H, WATER_T, 25, 20, debunk="demo-fc-1", reuse=wave1)
WATER_ORG = [
    [("A është e vërtetë që", "Je li istina da", "Is it true that"), ("Po lexoj që", "Čitam da", "I'm reading that"),
     ("Dikush shkroi që", "Neko piše da", "Someone wrote that")],
    [("uji në Mitrovicë është i helmuar", "je voda u Mitrovici otrovana", "the water in Mitrovica is poisoned"),
     ("uji i rubinetit në Mitrovicë nuk pihet", "voda iz česme u Mitrovici nije za piće", "Mitrovica tap water is unsafe"),
     ("ujësjellësi është kontaminuar", "je vodovod zagađen", "the water supply is contaminated")],
    [("?", "?", "?"), ("Po blej ujë në shishe.", "Kupujem flaširanu vodu.", "Buying bottled water."),
     ("Kam frikë për fëmijët.", "Plašim se za decu.", "Worried for the kids."), ("", "", "")],
]
organic_wave("water_poison", T + 0.3 * H, T + 8 * H, 85, WATER_ORG, debunk="demo-fc-1")
news("Kosova Times (demo)", T + 3 * H, "Ujësjellësi: lajmet për helmimin e ujit në Mitrovicë janë të rreme, uji është i sigurt për pije.",
     "Water utility: reports of water poisoning in Mitrovica are false, the water is safe to drink.", "water_poison")
news("Balkan Wire (demo)", T + 3.5 * H, "Mitrovica water utility says contamination rumours are false and tests show water is safe.",
     "", "water_poison")

# ------------------------------------------------------------------ 5. football (organic spike)
FOOT = [
    [("Çfarë ndeshje", "Kakva utakmica", "What a match"), ("Golat e sontëm", "Golovi večeras", "Tonight's goals"),
     ("Fitore e madhe", "Velika pobeda", "Big win"), ("Humbje e dhimbshme", "Bolan poraz", "Painful loss")],
    [("për Kombëtaren", "za reprezentaciju", "for the national team"), ("në stadium", "na stadionu", "at the stadium"),
     ("në minutën e fundit", "u poslednjem minutu", "in the last minute"), ("", "", "")],
    [("⚽", "⚽", "⚽"), ("Bravo djem!", "Bravo momci!", "Well done lads!"), ("Gjyqtari ishte i tmerrshëm.",
     "Sudija je bio užasan.", "The referee was terrible."), ("", "", "")],
]
organic_wave("football", START + 20 * H, START + 24 * H, 230, FOOT, shape="burst")

# ------------------------------------------------------------------ 6. KFOR withdrawal CAMPAIGN (same network)
T = START + 30 * H
KFOR_T = {
    "sr": ("KFOR se povlači sa Kosova sledeće nedelje, potvrđeno iz izvora u NATO. Pripremite se, niko nas neće štititi.",
           "KFOR is withdrawing from Kosovo next week, confirmed by NATO sources. Get ready, nobody will protect us."),
    "sq": ("KFOR po tërhiqet nga Kosova javën e ardhshme, konfirmuar nga burime në NATO. Përgatituni, askush s'do na mbrojë.",
           "KFOR is withdrawing from Kosovo next week, confirmed by NATO sources. Get ready, nobody will protect us."),
    "en": ("KFOR is withdrawing from Kosovo next week, confirmed by NATO sources. Prepare yourselves, nobody will protect us.", ""),
}
reuse = random.sample(wave1, 30) + random.sample([a for a in network if a not in wave1], 12)
campaign_burst("kfor_withdraw", T, KFOR_T, 38, 14, debunk="demo-fc-2", reuse=reuse)
KFOR_ORG = [
    [("A po largohet vërtet KFOR?", "Da li se KFOR stvarno povlači?", "Is KFOR really leaving?"),
     ("Lexova që KFOR po tërhiqet", "Pročitao sam da se KFOR povlači", "I read KFOR is withdrawing"),
     ("Thuhet se trupat e KFOR po largohen", "Priča se da KFOR napušta Kosovo", "Rumour says KFOR troops are pulling out")],
    [("nga Kosova", "sa Kosova", "from Kosovo"), ("javën tjetër", "sledeće nedelje", "next week"), ("", "", "")],
    [("Kjo është shqetësuese.", "Ovo je zabrinjavajuće.", "This is worrying."), ("Burim?", "Izvor?", "Source?"), ("", "", "")],
]
organic_wave("kfor_withdraw", T + 0.5 * H, T + 8 * H, 45, KFOR_ORG, debunk="demo-fc-2")
news("Balkan Wire (demo)", T + 4 * H, "NATO denies reports that KFOR is withdrawing from Kosovo, calls claims false.", "", "kfor_withdraw")

# ------------------------------------------------------------------ 7. power sabotage CAMPAIGN (distorts real event)
T = START + 36 * H
SAB_T = {
    "sr": ("Nestanak struje u Prištini nije bio kvar, to je bila sabotaža! Biće još napada na elektro mrežu, spremite agregate.",
           "The Pristina power outage was not a fault, it was sabotage! More attacks on the grid are coming, get generators."),
    "sq": ("Ndërprerja e rrymës në Prishtinë nuk ishte defekt, ishte sabotim! Do të ketë sulme të tjera në rrjet, bëhuni gati.",
           "The power cut in Pristina was not a fault, it was sabotage! There will be more attacks on the grid, get ready."),
    "en": ("The Pristina power outage was no accident, it was sabotage! More attacks on the electric grid are coming, be ready.", ""),
}
campaign_burst("power_sabotage", T, SAB_T, 32, 10, reuse=random.sample(network, 40))

# ------------------------------------------------------------------ 8. energy prices (steady, organic)
ENERGY = [
    [("Çmimi i rrymës", "Cena struje", "The electricity price"), ("Faturat e energjisë", "Računi za struju",
     "Energy bills"), ("Tarifat e reja", "Nove tarife", "The new tariffs")],
    [("po rritet sërish", "opet raste", "is rising again"), ("janë shumë të larta", "su previsoki", "are far too high"),
     ("nga janari", "od januara", "from January")],
    [("Si do t'ia dalim?", "Kako da preživimo?", "How will we manage?"), ("", "", ""), ("Qeveria duhet të ndihmojë.",
     "Vlada mora da pomogne.", "The government should help.")],
]
organic_wave("energy_prices", START, START + 48 * H, 110, ENERGY, shape="flat")

# ------------------------------------------------------------------ 9. noise (unrelated, mostly unique)
NOISE_SQ = ["Kafe e mirë sot në qytet", "Kush e pa filmin e ri", "Provimi i nesërm më shqetëson", "Darkë me familjen sonte",
            "Trafiku sot ishte normal", "Libri që po lexoj është i mrekullueshëm", "Koncert i mirë mbrëmë",
            "Po kërkoj banesë me qira", "Dita e parë në punë të re", "Recetë e re për flija", "Shëtitje në Gërmi",
            "Telefoni im i ri", "Kush shkon në palestër nesër", "Festë ditëlindjeje sonte", "Muzika e re shqipe"]
NOISE_SR = ["Dobra kafa danas u gradu", "Ko je gledao novi film", "Sutra ispit, nervozan sam", "Večera sa porodicom",
            "Saobraćaj danas normalan", "Knjiga koju čitam je odlična", "Sjajan koncert sinoć", "Tražim stan za izdavanje",
            "Prvi dan na novom poslu", "Nov recept za sarmu", "Šetnja pored reke", "Moj novi telefon",
            "Ko ide u teretanu sutra", "Rođendanska proslava večeras", "Nova muzika"]
NOISE_EN = ["Great coffee in town today", "Anyone seen the new film", "Exam tomorrow, nervous", "Family dinner tonight",
            "Traffic was fine today", "This book is brilliant", "Great concert last night", "Looking for a flat to rent",
            "First day at the new job", "Trying a new recipe", "Walk by the river", "My new phone",
            "Gym tomorrow anyone", "Birthday party tonight", "New music Friday"]
for _ in range(1500):
    lang = random.choices(["sq", "sr", "en"], [0.45, 0.4, 0.15])[0]
    base = {"sq": NOISE_SQ, "sr": NOISE_SR, "en": NOISE_EN}[lang]
    i = random.randrange(len(base))
    tail = random.choice(["", "", " :)", " 😊", f" #{random.randint(1, 999)}", " !", f" ({random.randint(1, 30)})"])
    emit(pick(organic[lang]), random.uniform(START, START + 48 * H), base[i] + tail,
         NOISE_EN[i] + tail, lang, "noise", source_type=pick(["x", "facebook", "instagram"]))

# ------------------------------------------------------------------ write
posts.sort(key=lambda p: p["ts"])
OUT.mkdir(exist_ok=True)
with open(OUT / "demo_posts.jsonl", "w", encoding="utf-8") as f:
    for p in posts:
        f.write(json.dumps(p, ensure_ascii=False) + "\n")

factchecks = [
    {"id": "demo-fc-1", "source": "DemoCheck (synthetic fact-checker)", "verdict": "False", "date": "2025-03-12",
     "url": "https://example.org/factcheck/water-mitrovica",
     "claim": "Claim that the Mitrovica water supply was poisoned and tap water is unsafe to drink. "
              "Uji në Mitrovicë është helmuar. Voda u Mitrovici je zatrovana."},
    {"id": "demo-fc-2", "source": "DemoCheck (synthetic fact-checker)", "verdict": "False", "date": "2025-09-02",
     "url": "https://example.org/factcheck/kfor-withdrawal",
     "claim": "Claim that KFOR is withdrawing from Kosovo next week. KFOR po tërhiqet nga Kosova. KFOR se povlači sa Kosova."},
    {"id": "demo-fc-3", "source": "DemoCheck (synthetic fact-checker)", "verdict": "False", "date": "2024-11-20",
     "url": "https://example.org/factcheck/euro-freeze",
     "claim": "Claim that banks will freeze or block euro accounts and savings. "
              "Bankat do të bllokojnë llogaritë në euro. Banke će zamrznuti račune u evrima."},
    {"id": "demo-fc-4", "source": "DemoCheck (synthetic fact-checker)", "verdict": "Misleading", "date": "2025-06-01",
     "url": "https://example.org/factcheck/airport",
     "claim": "Claim that Pristina airport will close permanently. Aeroporti i Prishtinës mbyllet. Aerodrom u Prištini se zatvara."},
]
with open(OUT / "factchecks_demo.json", "w", encoding="utf-8") as f:
    json.dump(factchecks, f, ensure_ascii=False, indent=1)

from collections import Counter
print(f"{len(posts)} posts, {len(accounts)} accounts")
print(Counter(p["gt_narrative"] for p in posts))
print("coordinated:", sum(p["gt_coordinated"] for p in posts), " debunk-repeating:", sum(1 for p in posts if p["gt_debunk"]))
