"""Generate bulk HELD-OUT weeks (4-8): 30 threat events each, drawn from templates written after the system was built.
Run once; do not change thresholds or keyword lists to fit them.

    py scripts/make_holdout_bulk.py --week 4     -> data/holdout4/ (posts, cyber, factchecks, truth.json)
    py scripts/run_fresh_tests.py                -> generates weeks 4-8, evaluates all fresh weeks, writes data/fresh_results.json
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import sys
WEEK = int(sys.argv[sys.argv.index("--week") + 1]) if "--week" in sys.argv else 4
random.seed(1000 + WEEK)
START = 1793491200 + WEEK * 7 * 86400  # week N after the demo week
H = 3600.0
DAY = 86400.0
OUT = Path(__file__).resolve().parent.parent / "data" / f"holdout{WEEK}"

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
    f"Lajme Kosova W{WEEK} (synthetic)": ("sq", "credible", 70000),
    f"Balkan Report W{WEEK} (synthetic)": ("en", "credible", 100000),
    f"Vesti Sever W{WEEK} (synthetic)": ("sr", "credible", 50000),
    f"Zëri Lajm W{WEEK} (synthetic)": ("sq", "credible", 60000),
}
LOWCRED_CHANNELS = {f"Alarm Info W{WEEK} (synthetic)": ("sq", 10000), f"Istina Sad W{WEEK} (synthetic)": ("sr", 9000),
                    f"Leaks Now W{WEEK} (synthetic)": ("en", 5000)}
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



# =================================================================== BULK HELD-OUT WEEKS (4-8)
# Each week: 30 threat events (7 infrastructure, 6 security incidents, 6 organised activity, 4 regional,
# 3 old debunked rumours, 4 coordinated campaigns of which 1 is hybrid), 3 harmless stories, 6 hacker claims.
# Stories are drawn at random from templates written after the system was built; places at random.
# Nothing in HybridWatch was tuned on these. Ground truth goes to truth.json.

M = 60.0
OUTS = list(OUTLETS)
POLICE = make_account(f"Kosovo Police W{WEEK} (synthetic)", START - 3000 * DAY, 150000, "sq")

# place: (Albanian, Serbian, English, gazetteer name)
PLACES = [("Prishtinë", "Priština", "Pristina", "Pristina"), ("Mitrovicë", "Mitrovica", "Mitrovica", "Mitrovica"),
          ("Zveçan", "Zvečan", "Zvečan", "Zvečan"), ("Zubin Potok", "Zubin Potok", "Zubin Potok", "Zubin Potok"),
          ("Leposaviq", "Leposavić", "Leposavić", "Leposavić"), ("Prizren", "Prizren", "Prizren", "Prizren"),
          ("Pejë", "Peć", "Peja", "Peja"), ("Gjakovë", "Đakovica", "Gjakova", "Gjakova"), ("Ferizaj", "Uroševac", "Ferizaj", "Ferizaj"),
          ("Gjilan", "Gnjilane", "Gjilan", "Gjilan"), ("Podujevë", "Podujevo", "Podujeva", "Podujeva"),
          ("Vushtrri", "Vučitrn", "Vushtrri", "Vushtrri"), ("Graçanicë", "Gračanica", "Gračanica", "Gračanica"),
          ("Lipjan", "Lipljan", "Lipjan", "Lipjan"), ("Drenas", "Glogovac", "Drenas", "Drenas"), ("Kaçanik", "Kačanik", "Kaçanik", "Kaçanik"),
          ("Dragash", "Dragaš", "Dragash", "Dragash"), ("Istog", "Istok", "Istog", "Istog"), ("Skenderaj", "Srbica", "Skenderaj", "Skenderaj"),
          ("Obiliq", "Obilić", "Obiliq", "Obiliq"), ("Fushë Kosovë", "Kosovo Polje", "Fushë Kosova", "Fushë Kosova")]

# event templates: (key, Albanian, Serbian, English, reaction slot). {a}/{s}/{e} = place in each language.
INFRA = [
    ("power", "Ndërprerje e rrymës në {a}, disa lagje pa energji", "Nestanak struje u opštini {s}, više naselja bez struje", "Power cut in {e}, several neighbourhoods without electricity"),
    ("water", "{a} pa ujë të pijshëm pas defektit në ujësjellës", "Opština {s} bez pijaće vode zbog kvara na vodovodu", "{e} without drinking water after a fault in the water supply"),
    ("landslide", "Rrëshqitje dheu bllokon rrugën afër {a}", "Klizište blokiralo put kod mesta {s}", "Landslide blocks the road near {e}"),
    ("bridge", "Ura në {a} mbyllet pas dëmtimeve në konstruksion", "Most u opštini {s} zatvoren zbog oštećenja konstrukcije", "Bridge in {e} closed after structural damage"),
    ("flood", "Përmbytje në {a}, rrugë dhe shtëpi nën ujë", "Poplave u opštini {s}, ulice i kuće pod vodom", "Flooding in {e}, streets and houses under water"),
    ("heating", "Mijëra banesa në {a} pa ngrohje pas defektit në ngrohtore", "Hiljade stanova u opštini {s} bez grejanja zbog kvara toplane", "Thousands of homes in {e} without heating after a plant fault"),
    ("isp", "Interneti ra në {a}, operatori po heton shkakun", "Pao internet u opštini {s}, operater istražuje uzrok", "Internet down in {e}, the operator is investigating"),
    ("substation", "Zjarr në nënstacionin elektrik në {a}, rryma e ndërprerë", "Požar u trafostanici u opštini {s}, struja isključena", "Fire at the electricity substation in {e}, power cut"),
    ("snow", "Bora izoloi fshatrat rreth {a}, rrugët të pakalueshme", "Sneg odsekao sela oko mesta {s}, putevi neprohodni", "Snow cuts off villages around {e}, roads impassable"),
]
SECURITY = [
    ("shooting", "Të shtëna në {a}, një person i plagosur", "Pucnjava u opštini {s}, jedna osoba ranjena", "Shooting in {e}, one person wounded"),
    ("device", "Mjet shpërthyes u gjet në {a}, policia e çaktivizoi", "Eksplozivna naprava pronađena u opštini {s}, policija je deaktivirala", "Explosive device found in {e}, police defused it"),
    ("grenade", "Granatë dore e hedhur në një lokal në {a}", "Ručna bomba bačena na lokal u opštini {s}", "Hand grenade thrown at a bar in {e}"),
    ("arson", "U dogj qëllimisht një veturë e policisë në {a}", "Namerno zapaljeno policijsko vozilo u opštini {s}", "Police car deliberately set on fire in {e}"),
    ("patrol", "U sulmua një patrullë e policisë në {a}", "Napadnuta policijska patrola u opštini {s}", "Police patrol attacked in {e}"),
    ("armed", "Persona të armatosur me maska u panë afër {a}", "Naoružani maskirani ljudi viđeni kod mesta {s}", "Armed masked men seen near {e}"),
    ("blast", "Shpërthim natën para një objekti zyrtar në {a}", "Eksplozija noću ispred zvanične zgrade u opštini {s}", "Night-time explosion outside an official building in {e}"),
]
ORGANIZED = [
    ("protest", "Protestuesit bllokuan rrugën kryesore në {a}", "Demonstranti blokirali glavni put u opštini {s}", "Protesters blocked the main road in {e}"),
    ("fuel", "Policia arrestoi kontrabandistë karburanti afër {a}", "Policija uhapsila švercere goriva kod mesta {s}", "Police arrest fuel smugglers near {e}"),
    ("cigarettes", "Kapen cigare kontrabandë në {a}, disa të arrestuar", "Zaplenjene švercovane cigarete u opštini {s}, više uhapšenih", "Smuggled cigarettes seized in {e}, several arrested"),
    ("druglab", "Policia zbuloi laborator droge në {a}", "Policija otkrila laboratoriju droge u opštini {s}", "Police uncover a drug lab in {e}"),
    ("migrants", "Arrestohen kontrabandistë me migrantë afër {a}", "Uhapšeni krijumčari migranata kod mesta {s}", "Migrant smugglers arrested near {e}"),
    ("weapons", "Policia mbylli një punishte ilegale armësh në {a}", "Policija zatvorila ilegalnu radionicu oružja u opštini {s}", "Police shut an illegal weapons workshop in {e}"),
    ("roadblock", "Kamionë dhe traktorë bllokojnë rrugën te {a} në protestë", "Kamioni i traktori blokiraju put kod mesta {s} u znak protesta", "Trucks and tractors block the road at {e} in protest"),
]
REGIONAL = [
    ("army_alert", "Serbia ngre ushtrinë në gatishmëri pranë vijës me Kosovën", "Srbija podigla vojsku u pripravnost kod administrativne linije", "Serbia puts its army on alert near the line with Kosovo"),
    ("trade_ban", "Serbia ndalon importin e mallrave nga Kosova", "Srbija zabranila uvoz robe sa Kosova", "Serbia bans imports of goods from Kosovo"),
    ("rs_crisis", "Republika Serbe bojkoton institucionet e Bosnjës", "Republika Srpska bojkotuje institucije BiH", "Republika Srpska boycotts Bosnia's state institutions"),
    ("mk_border", "Maqedonia e Veriut mbyll një pikëkalim me Kosovën", "Severna Makedonija zatvorila prelaz sa Kosovom", "North Macedonia closes a border crossing with Kosovo"),
    ("diplomats", "Serbia dëbon diplomatë pas një incidenti diplomatik", "Srbija proterala diplomate posle diplomatskog incidenta", "Serbia expels diplomats after a diplomatic row"),
    ("un_russia", "Rusia kërkon mbledhje urgjente të Këshillit të Sigurimit për Kosovën", "Rusija traži hitnu sednicu Saveta bezbednosti o Kosovu", "Russia calls an urgent UN Security Council meeting on Kosovo"),
    ("montenegro", "Protesta të dhunshme në Mal të Zi pranë kufirit me Kosovën", "Nasilni protesti u Crnoj Gori kod granice sa Kosovom", "Violent protests in Montenegro near the border with Kosovo"),
]
RUMOURS = [
    ("electricity_price", "çmimi i rrymës dyfishohet muajin tjetër", "cena struje se duplira sledećeg meseca", "the electricity price doubles next month"),
    ("passports", "pasaportat e Kosovës nuk vlejnë më në BE", "kosovski pasoši više ne važe u EU", "Kosovo passports are no longer valid in the EU"),
    ("salaries", "rrogat në sektorin publik do të ulen 30%", "plate u javnom sektoru biće smanjene 30%", "public-sector salaries will be cut by 30%"),
    ("petrol", "benzina do të mbarojë në pompa këtë javë", "benzina će nestati sa pumpi ove nedelje", "petrol stations will run dry this week"),
    ("phones", "do të vendoset taksë e re për çdo telefon", "uvodi se novi porez na svaki telefon", "a new tax on every phone is coming"),
    ("water_chem", "uji i rubinetit ka kimikate të rrezikshme", "voda iz česme sadrži opasne hemikalije", "tap water contains dangerous chemicals"),
]
CAMPAIGNS = [
    ("kfor_attack", "KFOR sulmoi civilë në {a}, ka të vdekur! Mediat heshtin, shpërndajeni.", "KFOR napao civile u opštini {s}, ima mrtvih! Mediji ćute, podelite.", "KFOR attacked civilians in {e}, people are dead! The media are silent, share."),
    ("army_crossed", "Ushtria serbe hyri në {a}! Burime të sigurta, përgatituni.", "Srpska vojska ušla u opštinu {s}! Sigurni izvori, spremite se.", "The Serbian army has entered {e}! Reliable sources, get ready."),
    ("hospital", "Spitali në {a} pa ilaçe, pacientët po vdesin! Qeveria e fsheh.", "Bolnica u opštini {s} bez lekova, pacijenti umiru! Vlada krije.", "The hospital in {e} has no medicine, patients are dying! The government hides it."),
    ("uranium", "Uji në {a} është ndotur me uranium, mos pini! Autoritetet heshtin.", "Voda u opštini {s} zagađena uranijumom, ne pijte! Vlasti ćute.", "Water in {e} contaminated with uranium, don't drink! Authorities silent."),
    ("nato_base", "NATO po ndërton bazë sekrete në {a}, toka u konfiskua! Shpërndajeni.", "NATO gradi tajnu bazu u opštini {s}, zemlja oduzeta! Podelite.", "NATO is building a secret base in {e}, land seized! Share."),
    ("protester_killed", "Policia vrau një protestues në {a}! Ata e fshehin, shpërndajeni.", "Policija ubila demonstranta u opštini {s}! Kriju to, podelite.", "Police killed a protester in {e}! They are hiding it, share."),
    ("votes_hacked", "KQZ u hakua, votat u ndryshuan në {a}! Zgjedhjet janë mashtrim.", "CIK hakovan, glasovi promenjeni u opštini {s}! Izbori su prevara.", "The election commission was hacked, votes changed in {e}! The election is a fraud."),
]
HARMLESS = [
    ("concert", ["Koncerti mbrëmë", "Koncert sinoć", "Last night's concert"], ["ishte fantastik", "bio je fantastičan", "was fantastic"]),
    ("food_fest", ["Festivali i ushqimit", "Festival hrane", "The food festival"], ["plot njerëz", "pun ljudi", "packed with people"]),
    ("tech_conf", ["Konferenca e teknologjisë", "Tehnološka konferencija", "The tech conference"], ["prezantime të mira", "dobra predavanja", "good talks"]),
    ("basketball", ["Ndeshja e basketbollit", "Košarkaška utakmica", "The basketball game"], ["fitore në fund", "pobeda na kraju", "won at the end"]),
    ("film", ["Premiera e filmit", "Premijera filma", "The film premiere"], ["salla plot", "puna sala", "full house"]),
    ("weather", ["Diell sot", "Sunčano danas", "Sunny today"], ["më në fund", "konačno", "finally"]),
]
OPENERS = [("", "", ""), ("", "", ""), ("Lajm:", "Vest:", "News:"), ("Sapo lexova:", "Upravo pročitah:", "Just read:"), ("Hmm,", "Hmm,", "Hmm,"),
           ("Shikoni:", "Gledajte:", "Look:"), ("Sot:", "Danas:", "Today:"), ("Dikush e konfirmon?", "Može li neko da potvrdi?", "Can anyone confirm?")]
REACTIONS = [("", "", ""), ("", "", ""), ("Çfarë dite.", "Kakav dan.", "What a day."), ("Kujdes të gjithë.", "Čuvajte se svi.", "Stay safe everyone."),
             ("Do të shohim.", "Videćemo.", "We'll see."), ("E pabesueshme.", "Neverovatno.", "Unbelievable."), ("Po ndjek lajmet.", "Pratim vesti.", "Following the news."),
             ("Shpresoj të rregullohet shpejt.", "Nadam se da će brzo rešiti.", "Hope it's sorted soon.")]

DETAILS = {
    "infrastructure": [("që nga mëngjesi", "od jutros", "since this morning"), ("prej dy orësh", "već dva sata", "for two hours now"),
                       ("thonë që po e rregullojnë", "kažu da popravljaju", "they say it's being fixed"), ("fqinjët po ankohen", "komšije se žale", "the neighbours are complaining"),
                       ("sërish!", "opet!", "again!"), ("askush s'po na tregon asgjë", "niko nam ništa ne govori", "nobody tells us anything"), ("", "", ""), ("", "", "")],
    "security_incident": [("policia në vend", "policija na licu mesta", "police on the scene"), ("dëgjova sirena", "čuo sam sirene", "I heard sirens"),
                          ("u tremba shumë", "baš sam se uplašio", "I got really scared"), ("shpresoj s'ka të lënduar", "nadam se da nema povređenih", "hope nobody's hurt"),
                          ("zona e rrethuar", "zona ograđena", "the area is cordoned off"), ("sërish tension", "opet napetost", "tension again"), ("", "", ""), ("", "", "")],
    "organized_activity": [("qindra njerëz", "stotine ljudi", "hundreds of people"), ("policia në terren", "policija na terenu", "police deployed"),
                           ("kush e organizoi?", "ko je ovo organizovao?", "who organised this?"), ("bravo policisë", "bravo policija", "well done police"),
                           ("trafiku i bllokuar", "saobraćaj blokiran", "traffic blocked"), ("më në fund", "konačno", "finally"), ("", "", ""), ("", "", "")],
    "regional": [("BE kërkon qetësi", "EU poziva na smirenost", "the EU calls for calm"), ("KFOR po monitoron", "KFOR prati situaciju", "KFOR is monitoring"),
                 ("shqetësuese", "zabrinjavajuće", "worrying"), ("çfarë do të thotë kjo për ne?", "šta ovo znači za nas?", "what does this mean for us?"),
                 ("rajoni po nxehet", "region se zahuktava", "the region is heating up"), ("", "", ""), ("", "", "")],
}


def head_variants(sq, sr, en):
    v = [(sq, sr, en)]
    if "," in sq and "," in sr and "," in en:
        v.append((sq.split(",")[0], sr.split(",")[0], en.split(",")[0]))
    v.append((sq.split(",")[0].lower(), sr.split(",")[0].lower(), en.split(",")[0].lower()))
    return v


truth = {"campaigns": [], "events": {}}
factchecks = []
cyber_events = []
used = set()


def place():
    return random.choice(PLACES)


def fill(t, p):
    return t.replace("{a}", p[0]).replace("{s}", p[1]).replace("{e}", p[2])


def unique_name(base):
    n = base
    k = 2
    while n in used:
        n = f"{base}_{k}"
        k += 1
    used.add(n)
    return n


def real_event(tpl, cat, hours, with_police):
    key, sq, sr, en = tpl
    p = place() if cat != "regional" else None
    narr = unique_name(f"{key}_{p[3] if p else 'regional'}".lower().replace(" ", "_"))
    T = START + hours * H
    txt = (lambda t: fill(t, p)) if p else (lambda t: t)
    news(OUTS[0], T, txt(sq) + ".", txt(en) + ".", narr)
    news(OUTS[1], T + random.uniform(20, 50) * M, txt(en) + ".", "", narr)
    if random.random() < 0.6:
        news(OUTS[2], T + random.uniform(30, 70) * M, txt(sr) + ".", txt(en) + ".", narr)
    if with_police:
        emit(POLICE, T + random.uniform(10, 60) * M, "Policia konfirmon: " + txt(sq).lower() + ".", "Police confirm: " + txt(en).lower() + ".",
             "sq", narr, source_type="official", credibility="credible", cyr_ok=False)
    n = random.randint(25, 70)
    organic_wave(narr, T + 5 * M, T + random.uniform(4, 8) * H, n, [OPENERS, head_variants(txt(sq), txt(sr), txt(en)), DETAILS[cat], REACTIONS])
    truth["events"][narr] = (cat, p[3] if p else None)
    return narr, p


def campaign(tpl, hours, accounts_pool, debunk, reuse=None, p=None):
    key, sq, sr, en = tpl
    p = p or place()
    narr = unique_name(f"camp_{key}_{p[3]}".lower().replace(" ", "_"))
    T = START + hours * H
    fc = None
    if debunk:
        fc = f"w{WEEK}-fc-{len(factchecks) + 1}"
        factchecks.append({"id": fc, "source": "Synthetic fact-checker", "verdict": "False", "date": "2025-06-01",
                           "url": f"https://example.org/w{WEEK}/factcheck/{len(factchecks) + 1}", "claim": f"Claim: {fill(en, p)} {fill(sq, p)} {fill(sr, p)}"})
    templ = {"sq": (fill(sq, p), fill(en, p)), "sr": (fill(sr, p), fill(en, p)), "en": (fill(en, p), "")}
    wave = campaign_burst(narr, T, templ, min(random.randint(28, 45), len(reuse or network)), random.randint(8, 15), debunk=fc, reuse=reuse)
    organic_wave(narr, T + 0.4 * H, T + 6 * H, random.randint(25, 45),
                 [OPENERS, [("A është e vërtetë që", "Da li je tačno da", "Is it true that")], [(fill(sq, p).split("!")[0].lower(), fill(sr, p).split("!")[0].lower(), fill(en, p).split("!")[0].lower())],
                  [("?", "?", "?"), ("Burim?", "Izvor?", "Source?"), ("", "", "")]], debunk=fc)
    news(OUTS[1], T + random.uniform(2.5, 4) * H, "Officials deny the claim: " + fill(en, p).split("!")[0] + " is false.", "", narr)
    truth["campaigns"].append(narr)
    truth["events"][narr] = ("disinformation", None)
    return wave, narr


def rumour(tpl):
    key, sq, sr, en = tpl
    narr = unique_name(f"rumour_{key}")
    fc = f"w{WEEK}-fc-{len(factchecks) + 1}"
    factchecks.append({"id": fc, "source": "Synthetic fact-checker", "verdict": "False", "date": "2024-12-01",
                       "url": f"https://example.org/w{WEEK}/factcheck/{len(factchecks) + 1}", "claim": f"Claim that {en}. {sq}. {sr}."})
    organic_wave(narr, START + random.uniform(3, 10) * H, START + 46 * H, random.randint(35, 55),
                 [OPENERS, [("Thonë që", "Kažu da", "They say"), ("Kujdes,", "Pazite,", "Careful,"), ("", "", "")], [(sq, sr, en)],
                  [("A është e vërtetë?", "Je li istina?", "Is it true?"), ("", "", "")]], shape="flat", debunk=fc)
    truth["events"][narr] = ("disinformation", None)


# ---------------- build the week
slots = sorted(random.sample(range(2, 42), 26))   # start hours for the 26 non-campaign... spread over 48 h
si = iter(slots)
for tpl in random.sample(INFRA, 6):
    real_event(tpl, "infrastructure", next(si), False)
for tpl in random.sample(SECURITY, 6):
    real_event(tpl, "security_incident", next(si), True)
for tpl in random.sample(ORGANIZED, 6):
    real_event(tpl, "organized_activity", next(si), random.random() < 0.6)
for tpl in random.sample(REGIONAL, 4):
    real_event(tpl, "regional", next(si), False)
for tpl in random.sample(RUMOURS, 3):
    rumour(tpl)

ctpl = random.sample(CAMPAIGNS, 4)
w1, _ = campaign(ctpl[0], random.uniform(6, 14), network, debunk=True)
campaign(ctpl[1], random.uniform(16, 24), network, debunk=False, reuse=random.sample(w1, min(25, len(w1))) + random.sample([a for a in network if a not in w1], 12))
campaign(ctpl[2], random.uniform(26, 32), network, debunk=True, reuse=random.sample(network, 45))

# hybrid: a real outage (7th infrastructure event) + a campaign calling it a cyber attack
HY = random.choice([("power", "energy", "power", ["rrym", "struj", "power", "electricity"], "Ndërprerje e rrymës në {a}", "Nestanak struje u opštini {s}", "Power cut in {e}"),
                    ("isp", "telecom", "network", ["internet", "rrjet", "mreža", "network"], "Interneti dhe rrjeti mobil ranë në {a}", "Pali internet i mobilna mreža u opštini {s}", "Internet and mobile network down in {e}"),
                    ("payments", "finance", "bank", ["bank", "kartel", "bankomat", "ATM"], "Pagesat me kartelë dhe bankomatet nuk punojnë në {a}", "Ne rade kartice i bankomati u opštini {s}", "Card payments and ATMs down in {e}")])
hkey, sector, concept, kws, hsq, hsr, hen = HY
hp = place()
TE = START + random.uniform(34, 40) * H
onarr = unique_name(f"outage_{hkey}_{hp[3]}".lower().replace(" ", "_"))
news(OUTS[3], TE + 20 * M, fill(hsq, hp) + ", operatori po heton.", fill(hen, hp) + ", the operator is investigating.", onarr)
news(OUTS[1], TE + 45 * M, fill(hen, hp) + "; cause under investigation.", "", onarr)
organic_wave(onarr, TE + 5 * M, TE + 5 * H, random.randint(40, 70), [OPENERS, head_variants(fill(hsq, hp), fill(hsr, hp), fill(hen, hp)), DETAILS['infrastructure'], REACTIONS])
truth["events"][onarr] = ("infrastructure", hp[3])
HC = (f"cyber_{hkey}", fill(hsq, hp) + " nuk është defekt, është sulm kibernetik! Sulmet e radhës vijnë së shpejti, bëhuni gati.",
      fill(hsr, hp) + " nije kvar, to je sajber napad! Sledeći napadi dolaze uskoro, spremite se.",
      fill(hen, hp) + " is no accident, it is a cyber attack! More attacks are coming, be ready.")
key, sq, sr, en = HC
hnarr = unique_name(f"camp_cyberclaim_{hkey}_{hp[3]}".lower().replace(" ", "_"))
campaign_burst(hnarr, TE + 30 * M, {"sq": (sq, en), "sr": (sr, en), "en": (en, "")}, random.randint(28, 36), 10, reuse=random.sample(network, 40))
truth["campaigns"].append(hnarr)
truth["events"][hnarr] = ("disinformation", None)

# ---------------- harmless
for key, a, b in random.sample(HARMLESS, 3):
    t0 = START + random.uniform(4, 40) * H
    organic_wave(unique_name(f"harmless_{key}"), t0, t0 + 5 * H, random.randint(120, 200), [OPENERS, [tuple(a)], [tuple(b), ("", "", "")], REACTIONS])
    truth["events"][f"harmless_{key}"] = (None, None)

# ---------------- noise
NOISE = [("Kafe me shokët", "Kafa sa društvom", "Coffee with friends"), ("Film sonte", "Film večeras", "Film tonight"), ("Darkë në shtëpi", "Večera kod kuće", "Dinner at home"),
         ("Ecje në park", "Šetnja u parku", "Walk in the park"), ("Libër i ri", "Nova knjiga", "New book"), ("Palestër sot", "Teretana danas", "Gym today"),
         ("Punë e re", "Novi posao", "New job"), ("Recetë e re", "Novi recept", "New recipe"), ("Telefon i ri", "Novi telefon", "New phone"), ("Ditëlindje sot", "Rođendan danas", "Birthday today")]
for _ in range(1500):
    lang = random.choices(["sq", "sr", "en"], [0.45, 0.4, 0.15])[0]
    t = random.choice(NOISE)
    emit(pick(organic[lang]), random.uniform(START, START + 48 * H), t[{"sq": 0, "sr": 1, "en": 2}[lang]] + random.choice(["", "", " :)", " !", f" #{random.randint(1, 99)}"]),
         t[2], lang, "noise", source_type=pick(["x", "facebook", "instagram"]))

# ---------------- hacker claims (6): hybrid outage + 5 drawn from claim types
HACK = make_account(f"hacktivist_w{WEEK}", START - 14 * DAY, 3000, "mix")


def probes(t0, pattern, url, name="availability"):
    return [{"check_name": name, "result": r, "detail": d, "evidence_url": url, "checked_at": round(t0 + m * M, 1)} for m, r, d in pattern]


def hack_post(ts, sq, en, narr):
    emit(HACK, ts, sq, en, "sq", narr, source_type="telegram", credibility="low", cyr_ok=False)


cyber_events.append({"ext_id": f"w{WEEK}-cyber-hybrid", "kind": "outage", "title": fill(hen, hp), "target": f"{hkey} operator in {hp[2]}" + " (synthetic)",
                     "sector": sector, "claimed_actor": None, "source_name": "IODA / status page (recorded, synthetic)", "source_type": "ioda",
                     "source_url": "https://example.org/status", "claimed_at": TE, "first_seen": TE + 6 * M,
                     "raw": {"synthetic": True, "keywords": kws + [hp[0].lower(), hp[2].lower()], "gt_status": "confirmed", "gt_hybrid": hnarr,
                             "recorded_checks": [{"check_name": "ioda" if hkey != "payments" else "availability", "result": "supports", "checked_at": round(TE + 6 * M, 1),
                                                  "detail": "Outage signal confirmed (synthetic)", "evidence_url": "https://example.org/status"}]}})
TARGETS = [("Ministry of Health website", "ministria e shëndetësisë", "health"), ("Kosovo Tax Administration website", "atk", "government"),
           ("Municipality of {e} website", "komuna", "government"), ("Demo Bank Kosova", "demo bank", "finance"), ("Demo Telecom", "demo telecom", "telecom"),
           ("Kosovo Energy Distribution website", "keds", "energy"), ("University of {e} website", "universitet", "government")]
KINDS = random.sample(["ddos_true", "ddos_false", "deface_false", "ransom_new", "ransom_repost", "breach_open", "kev"], 5)
for i, kind in enumerate(KINDS):
    t = START + (4 + i * 8 + random.uniform(0, 3)) * H
    p = place()
    tname, kw, sec = random.choice(TARGETS)
    tname = tname.replace("{e}", p[2])
    narr = f"cyw{WEEK}_{i}"
    dom = f"t{i}w{WEEK}.example.org"
    ev = {"ext_id": f"w{WEEK}-cyber-{i}", "target": tname + " (synthetic)", "sector": sec, "claimed_at": t, "first_seen": t + 3 * M,
          "source_url": f"https://example.org/w{WEEK}/{i}", "claimed_actor": f"hacktivist_w{WEEK}", "source_name": f"t.me/hacktivist_w{WEEK}", "source_type": "hacktivist"}
    if kind == "ddos_true":
        hack_post(t, f"E rrëzuam {tname}! #DDoS", f"We took down {tname}! #DDoS", narr)
        news(OUTS[0], t + 50 * M, f"{tname} ishte jashtë funksionit pas një sulmi DDoS.", f"{tname} was down after a DDoS attack.", narr)
        ev.update(kind="ddos_claim", title=f"Hacktivists claim DDoS on {tname}", raw={"synthetic": True, "domain": dom, "keywords": [kw, tname.lower()], "gt_status": "confirmed",
                  "recorded_checks": probes(t, [(4, "supports", "Not reachable (timeout)"), (20, "supports", "Not reachable (HTTP 503)"), (50, "contradicts", "Up again")], f"https://{dom}/")})
    elif kind == "ddos_false":
        hack_post(t, f"{tname} është poshtë! #DDoS", f"{tname} is down! #DDoS", narr)
        ev.update(kind="ddos_claim", title=f"Hacktivists claim DDoS on {tname}", raw={"synthetic": True, "domain": dom, "keywords": [kw], "gt_status": "refuted",
                  "recorded_checks": probes(t, [(3, "contradicts", "Up (HTTP 200)"), (15, "contradicts", "Up (HTTP 200)"), (35, "contradicts", "Up (HTTP 200)")], f"https://{dom}/")})
    elif kind == "deface_false":
        hack_post(t, f"Hakuam {tname}! #defaced", f"We hacked {tname}!", narr)
        ev.update(kind="defacement", title=f"Hacktivists claim defacement of {tname}", raw={"synthetic": True, "domain": dom, "keywords": [kw], "gt_status": "refuted",
                  "recorded_checks": probes(t, [(5, "contradicts", "Up (HTTP 200)"), (25, "contradicts", "Up (HTTP 200)")], f"https://{dom}/")
                  + probes(t, [(5, "contradicts", "Content unchanged"), (25, "contradicts", "Content unchanged")], f"https://{dom}/", name="content_hash")})
    elif kind == "ransom_new":
        news(OUTS[3], t + 3 * H, f"{tname} konfirmon sulm me ransomware.", f"{tname} confirms a ransomware attack.", narr)
        emit(POLICE, t + 4 * H, f"Policia heton sulmin me ransomware ndaj {tname}.", f"Police are investigating the ransomware attack on {tname}.", "sq", narr,
             source_type="official", credibility="credible", cyr_ok=False)
        ev.update(kind="ransomware_claim", title=f"Ransomware listing: {tname}", source_type="ransomware_tracker", source_name="Ransomware tracker (recorded, synthetic)",
                  raw={"synthetic": True, "country": "XK", "description": f"{tname}, Kosovo", "keywords": [tname.lower(), kw], "gt_status": "confirmed"})
    elif kind == "ransom_repost":
        ev.update(kind="ransomware_claim", title=f"Ransomware listing: {tname}", source_type="ransomware_tracker", source_name="Ransomware tracker (recorded, synthetic)",
                  raw={"synthetic": True, "country": "XK", "description": f"{tname}, Kosovo", "previously_listed_at": "5 May 2024",
                       "previous_listing_url": "https://example.org/old", "gt_status": "refuted"})
    elif kind == "breach_open":
        hack_post(t, f"Kemi të dhënat e {tname}. Së shpejti. #leak", f"We have {tname}'s data. Soon. #leak", narr)
        ev.update(kind="data_breach_claim", title=f"Hacktivists claim a data leak at {tname}", raw={"synthetic": True, "country": "XK", "keywords": [kw], "gt_status": "unverified"})
    else:
        cve = random.choice([("CVE-2023-20198", "Cisco", "IOS XE"), ("CVE-2024-3400", "Palo Alto Networks", "PAN-OS"), ("CVE-2023-34362", "Progress", "MOVEit Transfer"),
                             ("CVE-2022-41040", "Microsoft", "Exchange Server")])
        ev.update(kind="vulnerability", title=f"{cve[0]} ({cve[1]} {cve[2]}) actively exploited", target=f"{cve[1]} {cve[2]}", sector="other", claimed_actor=None,
                  source_type="kev", source_name="CISA KEV (recorded, synthetic)",
                  raw={"synthetic": True, "cve": cve[0], "vendor": cve[1], "product": cve[2], "gt_status": "confirmed",
                       "recorded_checks": [{"check_name": "kev", "result": "supports", "checked_at": round(t, 1), "detail": f"{cve[0]} listed as actively exploited", "evidence_url": "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"}]})
    cyber_events.append(ev)

# ---------------- write
OUT.mkdir(parents=True, exist_ok=True)
posts.sort(key=lambda p: p["ts"])
with open(OUT / "posts.jsonl", "w", encoding="utf-8") as f:
    for p in posts:
        f.write(json.dumps(p, ensure_ascii=False) + "\n")
with open(OUT / "cyber.json", "w", encoding="utf-8") as f:
    json.dump(cyber_events, f, ensure_ascii=False, indent=1)
with open(OUT / "factchecks.json", "w", encoding="utf-8") as f:
    json.dump(factchecks, f, ensure_ascii=False, indent=1)
with open(OUT / "truth.json", "w", encoding="utf-8") as f:
    json.dump(truth, f, ensure_ascii=False, indent=1)
n_threat = sum(1 for c, _ in truth["events"].values() if c)
print(f"week {WEEK}: {len(posts)} posts, {n_threat} threat events, {len(truth['campaigns'])} campaigns, {len(cyber_events)} hacker claims")
