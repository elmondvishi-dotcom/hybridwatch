"""Generate HELD-OUT test 3: a third unseen synthetic week with 24 threat events.
Run once; do not change thresholds or keyword lists to fit it.

    py scripts/make_holdout3_data.py          -> data/holdout3/
    py scripts/evaluate_holdout.py --set 3    -> results
"""
from __future__ import annotations

import json
import random
from pathlib import Path

random.seed(9090)
START = 1795305600  # 2026-11-22 00:00 UTC (a third new week)
H = 3600.0
DAY = 86400.0
OUT = Path(__file__).resolve().parent.parent / "data" / "holdout3"

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
    "Kosovapress Demo (holdout3)": ("sq", "credible", 75000),
    "Balkan Brief (holdout3)": ("en", "credible", 90000),
    "Kosovo Sever Info (holdout3)": ("sr", "credible", 40000),
    "Gazeta Lindja (holdout3)": ("sq", "credible", 55000),
}
LOWCRED_CHANNELS = {"Lajm Alarm (holdout3)": ("sq", 10000), "Glas Istine (holdout3)": ("sr", 9000),
                    "Leak Balkans (holdout3)": ("en", 4000)}
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



# =================================================================== HELD-OUT SCENARIO 3
# A third unseen week (bigger: 24 threat events), written after tests 1 and 2. Run once; nothing tuned for it.
# Same mix of threat types; place names are reused freely because real events repeat in the same towns.

# Organic posts need natural variety: real people rarely post word-for-word identical text. Every organic wave in
# this test gets an opener and a reaction slot on top of its own wording (tests 1 and 2 had this variety built in).
OPENERS = [("", "", ""), ("", "", ""), ("Lajm:", "Vest:", "News:"), ("Sapo lexova:", "Upravo pročitah:", "Just read:"), ("Hmm,", "Hmm,", "Hmm,"),
           ("Shikoni:", "Gledajte:", "Look:"), ("Sot:", "Danas:", "Today:"), ("Dikush e konfirmon?", "Može li neko da potvrdi?", "Can anyone confirm?")]
REACTIONS = [("", "", ""), ("", "", ""), ("Çfarë dite.", "Kakav dan.", "What a day."), ("Kujdes të gjithë.", "Čuvajte se svi.", "Stay safe everyone."),
             ("Do të shohim.", "Videćemo.", "We'll see."), ("E pabesueshme.", "Neverovatno.", "Unbelievable."), ("Faleminderit për info.", "Hvala na info.", "Thanks for the info."),
             ("Po ndjek lajmet.", "Pratim vesti.", "Following the news.")]
_organic_wave = organic_wave


def organic_wave(narrative, t0, t1, n, parts, **kw):
    return _organic_wave(narrative, t0, t1, n, [OPENERS] + parts + [REACTIONS], **kw)


POLICE = make_account("Kosovo Police (holdout3)", START - 3000 * DAY, 150000, "sq")
M = 60.0
OUTS = list(OUTLETS)


def official(author, ts, text, en, narrative, lang="sq"):
    emit(author, ts, text, en, lang, narrative, source_type="official", credibility="credible", cyr_ok=False)


def story(narr, hours, items, parts, n, police=None, dur=6):
    """items: list of (outlet index, minutes after start, text, english). police: (minutes, sq text, en text)."""
    T = START + hours * H
    for oi, m, txt, en in items:
        news(OUTS[oi], T + m * M, txt, en, narr)
    if police:
        official(POLICE, T + police[0] * M, police[1], police[2], narr)
    organic_wave(narr, T + 5 * M, T + dur * H, n, parts)


# outlets: 0 sq, 1 en, 2 sr, 3 sq
# ---------------- infrastructure (7)
story("water_vushtrri", 2, [(0, 0, "Vushtrria pa ujë të pijshëm pas defektit në fabrikën e ujit.", "Vushtrri without drinking water after a fault at the water plant."),
                            (1, 30, "Vushtrri residents left without drinking water after a fault at the town's water plant.", "")],
      [[("Pa ujë në Vushtrri", "Nema vode u Vučitrnu", "No water in Vushtrri")], [("që nga mëngjesi", "od jutros", "since morning"), ("", "", "")],
       [("Kur kthehet?", "Kada će biti?", "When will it be back?"), ("", "", "")]], 70)
story("bridge_mitrovica", 5, [(2, 0, "Most na Ibru u Mitrovici zatvoren zbog pukotina u konstrukciji.", "The Ibar bridge in Mitrovica closed because of cracks in the structure."),
                              (0, 40, "Ura mbi Ibër në Mitrovicë mbyllet për shkak të çarjeve në konstruksion.", "The bridge over the Ibar in Mitrovica closes due to cracks in the structure.")],
      [[("Ura në Mitrovicë e mbyllur", "Most u Mitrovici zatvoren", "The Mitrovica bridge is closed")], [("për shkak të çarjeve", "zbog pukotina", "because of cracks"), ("", "", "")],
       [("Si do të kalojmë?", "Kako ćemo preći?", "How do we cross now?"), ("", "", "")]], 60)
story("snow_dragash", 8, [(3, 0, "Stuhia e borës la pa rrymë disa fshatra në Dragash.", "The snowstorm left several villages in Dragash without power."),
                          (1, 50, "Snowstorm cuts power to villages around Dragash in southern Kosovo.", "")],
      [[("Pa rrymë në Dragash", "Bez struje u Dragašu", "No power in Dragash"), ("Borë e madhe në Dragash", "Veliki sneg u Dragašu", "Heavy snow in Dragash")],
       [("fshatrat të izoluar", "sela odsečena", "villages cut off"), ("", "", "")]], 60)
story("train_fushe", 11, [(0, 0, "Treni Prishtinë–Pejë doli nga shinat afër Fushë Kosovës, linja pezullohet.", "The Pristina–Peja train derailed near Fushë Kosova, the line is suspended."),
                          (1, 35, "Train derails near Fushë Kosova; the Pristina–Peja line is suspended, no injuries.", "")],
      [[("Treni doli nga shinat te Fushë Kosova", "Voz iskočio iz šina kod Kosova Polja", "Train off the rails near Fushë Kosova")],
       [("nuk ka të lënduar", "nema povređenih", "no injuries"), ("linja e pezulluar", "linija obustavljena", "line suspended"), ("", "", "")]], 45)
story("gas_pristina", 14, [(3, 0, "Rrjedhje gazi në një ndërtesë në Prishtinë, banorët u evakuuan.", "Gas leak in a building in Pristina, residents evacuated."),
                           (2, 30, "Curenje gasa u zgradi u Prištini, stanari evakuisani.", "Gas leak in a building in Pristina, residents evacuated.")],
      [[("Rrjedhje gazi në Prishtinë", "Curenje gasa u Prištini", "Gas leak in Pristina")], [("ndërtesa u evakuua", "zgrada evakuisana", "building evacuated"), ("", "", "")]], 40)
story("snow_kacanik", 20, [(0, 0, "Bora mbyll rrugën nëpër grykën e Kaçanikut, kamionët të bllokuar.", "Snow closes the road through the Kaçanik gorge, trucks stuck."),
                           (1, 45, "Snow closes the Kaçanik gorge road; trucks stranded on the route to Skopje.", "")],
      [[("Gryka e Kaçanikut e mbyllur", "Klisura kod Kačanika zatvorena", "Kaçanik gorge closed")], [("kamionë të bllokuar", "kamioni zaglavljeni", "trucks stuck"), ("", "", "")]], 50)

# ---------------- security incidents (4)
story("shooting_obiliq", 3, [(1, 20, "One man wounded in a shooting in Obiliq; police search for the gunman.", "")],
      [[("Të shtëna në Obiliq", "Pucnjava u Obiliću", "Shooting in Obiliq")], [("një i plagosur", "jedan ranjen", "one wounded"), ("policia në kërkim", "policija traga", "police searching"), ("", "", "")]],
      35, police=(40, "Një person u plagos me armë zjarri në Obiliq. Policia po kërkon autorin.", "A person was shot and wounded in Obiliq. Police are searching for the attacker."))
story("explosive_skenderaj", 9, [(3, 15, "Mjet shpërthyes u gjet pranë një shkolle në Skenderaj, nxënësit u evakuuan.", "Explosive device found near a school in Skenderaj, pupils evacuated.")],
      [[("Bombë pranë shkollës në Skenderaj", "Bomba kod škole u Srbici", "Bomb near a school in Skenderaj")], [("nxënësit u evakuuan", "đaci evakuisani", "pupils evacuated"), ("", "", "")]],
      40, police=(30, "Policia çaktivizoi një mjet shpërthyes pranë një shkolle në Skenderaj.", "Police defused an explosive device near a school in Skenderaj."))
story("arson_istog", 17, [(2, 20, "Zapaljeno policijsko vozilo u Istoku, policija traga za počiniocima.", "Police vehicle set on fire in Istog, police searching for the perpetrators.")],
      [[("U dogj vetura e policisë në Istog", "Zapaljen policijski auto u Istoku", "Police car set on fire in Istog")], [("sulm i qëllimshëm", "namerni napad", "deliberate attack"), ("", "", "")]],
      30, police=(45, "Një veturë e policisë u dogj qëllimisht në Istog. Hetimet vazhdojnë.", "A police car was deliberately burned in Istog. Investigation ongoing."))
story("armed_banjska", 29, [(1, 15, "Police deploy near Banjska after reports of armed masked men in the area.", ""),
                            (2, 30, "Policija u Banjskoj nakon dojava o naoružanim maskiranim ljudima.", "Police in Banjska after reports of armed masked men.")],
      [[("Persona të armatosur te Banjska", "Naoružani ljudi kod Banjske", "Armed men near Banjska")], [("policia në terren", "policija na terenu", "police deployed"), ("", "", "")]],
      35, police=(40, "Policia është vendosur afër Banjskës pas raportimeve për persona të armatosur me maska.", "Police deployed near Banjska after reports of armed masked people."))

# ---------------- organized activity (5)
story("protest_mitrovica", 12, [(2, 10, "Stotine ljudi blokirale prilaz mostu u Mitrovici u znak protesta.", "Hundreds of people blocked the approach to the bridge in Mitrovica in protest."),
                                (0, 40, "Qindra protestues bllokuan hyrjen në urë në Mitrovicë.", "Hundreds of protesters blocked access to the bridge in Mitrovica.")],
      [[("Protestë te ura në Mitrovicë", "Protest kod mosta u Mitrovici", "Protest at the Mitrovica bridge")], [("qindra njerëz", "stotine ljudi", "hundreds of people"), ("", "", "")]], 55)
story("fuel_jarinje", 15, [(1, 0, "Police arrest four for smuggling fuel through the Jarinje crossing.", "")],
      [[("Kontrabandë karburanti te Jarinja", "Šverc goriva kod Jarinja", "Fuel smuggling at Jarinje")], [("katër të arrestuar", "četvorica uhapšena", "four arrested")]],
      12, police=(10, "Policia arrestoi katër persona për kontrabandë karburanti në pikëkalimin e Jarinjës.", "Police arrested four people for fuel smuggling at the Jarinje crossing."))
story("teachers_gjakova", 22, [(3, 0, "Mësuesit bllokuan rrugën kryesore në Gjakovë në protestë për paga.", "Teachers blocked the main road in Gjakova in a protest over pay."),
                               (1, 40, "Teachers block the main road in Gjakova in a pay protest.", "")],
      [[("Mësuesit në protestë në Gjakovë", "Nastavnici protestuju u Đakovici", "Teachers protesting in Gjakova")], [("rruga e bllokuar", "put blokiran", "road blocked"), ("", "", "")]], 50)
story("drugs_fushe", 25, [(0, 20, "Policia zbulon laborator droge në Fushë Kosovë, tre të arrestuar.", "Police discover a drug lab in Fushë Kosova, three arrested.")],
      [[("Laborator droge në Fushë Kosovë", "Laboratorija droge u Kosovu Polju", "Drug lab in Fushë Kosova")], [("tre të arrestuar", "trojica uhapšena", "three arrested")]],
      12, police=(10, "Policia zbuloi një laborator droge në Fushë Kosovë dhe arrestoi tre persona.", "Police found a drug lab in Fushë Kosova and arrested three people."))
story("weapons_drenas", 33, [(1, 20, "Police raid an illegal weapons workshop in Drenas, five arrested.", "")],
      [[("Punishte armësh në Drenas", "Radionica oružja u Glogovcu", "Weapons workshop in Drenas")], [("pesë të arrestuar", "petorica uhapšena", "five arrested")]],
      12, police=(10, "Policia mbylli një punishte ilegale armësh në Drenas, pesë të arrestuar.", "Police shut an illegal weapons workshop in Drenas, five arrested."))

# ---------------- regional (3)
story("serbia_trade", 18, [(2, 0, "Srbija suspendovala uvoz robe sa Kosova do daljnjeg.", "Serbia suspended imports of goods from Kosovo until further notice."),
                           (1, 40, "Serbia suspends imports of goods from Kosovo until further notice.", "")],
      [[("Serbia ndalon mallrat nga Kosova", "Srbija zabranila robu sa Kosova", "Serbia bans goods from Kosovo")], [("deri në njoftim të ri", "do daljnjeg", "until further notice"), ("", "", "")]], 30)
story("rs_police", 27, [(1, 0, "Republika Srpska police block state prosecutors from entering a ministry in Banja Luka.", ""),
                        (2, 40, "Policija RS sprečila državne tužioce da uđu u ministarstvo u Banjaluci.", "RS police stopped state prosecutors from entering a ministry in Banja Luka.")],
      [[("Policia e RS bllokon prokurorët", "Policija RS blokirala tužioce", "RS police block prosecutors")], [("krizë në Bosnjë", "kriza u BiH", "crisis in Bosnia"), ("", "", "")]], 25)
story("serbia_alert", 39, [(2, 0, "Predsednik Srbije naredio najviši stepen borbene gotovosti vojske.", "The Serbian president ordered the army's highest level of combat readiness."),
                           (1, 30, "Serbia's president orders the army to its highest level of combat readiness.", "")],
      [[("Serbia ngre ushtrinë në gatishmëri", "Srbija podigla vojsku u pripravnost", "Serbia puts its army on alert")], [("KFOR po monitoron", "KFOR prati", "KFOR is monitoring"), ("", "", "")]], 35)

# ---------------- disinformation: old debunked rumours, organic (2)
organic_wave("conscription_rumour", START + 4 * H, START + 44 * H, 45, [
    [("Thonë që", "Kažu da", "They say"), ("", "", "")],
    [("shërbimi ushtarak bëhet i detyrueshëm për të gjithë djemtë 18 vjeç", "vojni rok postaje obavezan za sve momke od 18", "military service becomes mandatory for all 18-year-old men")],
    [("nga vera", "od leta", "from summer"), ("A është e vërtetë?", "Je li istina?", "Is it true?"), ("", "", "")]], shape="flat", debunk="hold3-fc-3")
organic_wave("school_virus_rumour", START + 10 * H, START + 46 * H, 40, [
    [("Kujdes,", "Pazite,", "Careful,"), ("", "", "")],
    [("shkollat mbyllen dy javë për shkak të një virusi", "škole se zatvaraju dve nedelje zbog virusa", "schools will close for two weeks because of a virus")],
    [("nga e hëna", "od ponedeljka", "from Monday"), ("", "", "")]], shape="flat", debunk="hold3-fc-4")

# ---------------- campaigns (3), one hybrid
T = START + 13 * H
NATO_T = {"sr": ("NATO sprema novo bombardovanje Srbije, Kosovo će biti baza za napad! Spremite se, podelite.", "NATO is preparing a new bombing of Serbia, Kosovo will be the base for the attack! Get ready, share."),
          "sq": ("NATO po përgatit bombardim të ri të Serbisë, Kosova do të jetë baza e sulmit! Përgatituni.", "NATO is preparing a new bombing of Serbia, Kosovo will be the attack base! Get ready."),
          "en": ("NATO is preparing to bomb Serbia again and Kosovo will be the launch base! Get ready, share.", "")}
nwave = campaign_burst("nato_bombing", T, NATO_T, 42, 12, debunk="hold3-fc-1")
organic_wave("nato_bombing", T + 0.4 * H, T + 6 * H, 40, [[("A do të bombardojë NATO sërish?", "Da li će NATO opet bombardovati?", "Will NATO bomb again?")],
                                                         [("Burim?", "Izvor?", "Source?"), ("", "", "")]], debunk="hold3-fc-1")
news(OUTS[1], T + 3 * H, "NATO calls claims of a planned strike on Serbia from Kosovo completely false.", "", "nato_bombing")

T = START + 30 * H
EXP_T = {"sr": ("Srbi sa severa moraće da napuste svoje kuće do proleća, naređenje iz Prištine! Ne dozvolite, podelite svima.", "Serbs in the north will have to leave their homes by spring, an order from Pristina! Don't allow it, share with everyone."),
         "sq": ("Serbët e veriut do të detyrohen të largohen nga shtëpitë deri në pranverë, urdhër nga Prishtina! Shpërndajeni.", "Serbs in the north will be forced to leave their homes by spring, an order from Pristina! Share it."),
         "en": ("Serbs in northern Kosovo will be forced out of their homes by spring on Pristina's orders! Share with everyone.", "")}
campaign_burst("expulsion_claim", T, EXP_T, 36, 14, reuse=random.sample(nwave, 28) + random.sample([a for a in network if a not in nwave], 10))
organic_wave("expulsion_claim", T + 0.5 * H, T + 6 * H, 40, [[("A është e vërtetë që serbët do të dëbohen nga veriu?", "Da li je istina da će Srbi biti proterani sa severa?", "Is it true Serbs will be expelled from the north?")],
                                                            [("Kjo është e rrezikshme.", "Ovo je opasno.", "This is dangerous."), ("", "", "")]])
news(OUTS[2], T + 3 * H, "Vlada Kosova: tvrdnje o proterivanju Srba sa severa su potpuno lažne.", "Kosovo government: claims about expelling Serbs from the north are completely false.", "expulsion_claim")

# hybrid: customs IT outage, then "customs hacked" campaign
TE = START + 35 * H
story("customs_outage", 35, [(0, 15, "Sistemi i Doganës së Kosovës nuk funksionon, kamionët presin në të gjitha pikëkalimet.", "Kosovo Customs' system is down, trucks waiting at all crossings."),
                             (1, 40, "Kosovo Customs IT system down; long truck queues at border crossings.", "")],
      [[("Sistemi i doganës ka rënë", "Pao carinski sistem", "The customs system is down")], [("kamionët në radhë", "kamioni u redu", "trucks queuing"), ("", "", "")]], 60, dur=5)
CUS_T = {"sq": ("Dogana nuk ka defekt, u hakua nga rusët! Të gjitha të dhënat u vodhën, kufijtë do të mbyllen për javë.", "Customs has no fault, it was hacked by the Russians! All data was stolen, the borders will be closed for weeks."),
         "sr": ("Carina nije u kvaru, hakovali su je Rusi! Svi podaci su ukradeni, granice će biti zatvorene nedeljama.", "Customs is not broken, Russians hacked it! All data stolen, borders will be closed for weeks."),
         "en": ("Kosovo Customs isn't down by accident, Russian hackers broke in! All data stolen, borders closed for weeks.", "")}
campaign_burst("customs_hack_claim", TE + 30 * M, CUS_T, 30, 10, reuse=random.sample(network, 40))

# ---------------- harmless (3)
organic_wave("eurovision", START + 21 * H, START + 25 * H, 180, [[("Kënga për Eurovizion", "Pesma za Evroviziju", "The Eurovision song")], [("është e mrekullueshme", "je odlična", "is great"), ("nuk më pëlqen", "ne sviđa mi se", "I don't like it")]])
organic_wave("mall_opening", START + 26 * H, START + 30 * H, 140, [[("Qendra e re tregtare", "Novi tržni centar", "The new shopping mall")], [("hapet sot", "otvara se danas", "opens today"), ("radhë të gjata", "dugi redovi", "long queues")]])
organic_wave("coffee_prices", START, START + 48 * H, 90, [[("Çmimi i kafesë", "Cena kafe", "The price of coffee")], [("u rrit sërish", "opet porasla", "went up again")]], shape="flat")

# ---------------- noise
NOISE_SQ = ["Mëngjes i qetë", "Seriali i ri", "Futboll me shokët", "Byrek për darkë", "Ecje me qenin", "Poezi e bukur", "Muzikë live", "Punë nga shtëpia", "Pite me spinaq", "Laptop i ri", "Joga sot", "Fejesa e shoqes"]
NOISE_SR = ["Miran jutro", "Nova serija", "Fudbal sa drugarima", "Burek za večeru", "Šetnja sa psom", "Lepa pesma", "Muzika uživo", "Rad od kuće", "Pita sa spanaćem", "Novi laptop", "Joga danas", "Veridba drugarice"]
NOISE_EN = ["Quiet morning", "New series", "Football with friends", "Pastry for dinner", "Walking the dog", "Nice poem", "Live music", "Working from home", "Spinach pie", "New laptop", "Yoga today", "Friend's engagement"]
for _ in range(1500):
    lang = random.choices(["sq", "sr", "en"], [0.45, 0.4, 0.15])[0]
    base = {"sq": NOISE_SQ, "sr": NOISE_SR, "en": NOISE_EN}[lang]
    i = random.randrange(len(base))
    emit(pick(organic[lang]), random.uniform(START, START + 48 * H), base[i] + random.choice(["", "", " :)", " !"]), NOISE_EN[i], lang, "noise",
         source_type=pick(["x", "facebook", "instagram"]))

# ---------------- CYBER (6)
HACK = make_account("holdout3_hacktivist", START - 20 * DAY, 3500, "mix")
SYN = " (synthetic)"
cyber_events = []


def cyber_post(author, ts, text, en, lang, narrative, source_type, credibility):
    emit(author, ts, text, en, lang, narrative, source_type=source_type, credibility=credibility, cyr_ok=False)


def probes(t0, pattern, url, name="availability"):
    return [{"check_name": name, "result": r, "detail": d, "evidence_url": url, "checked_at": round(t0 + m * M, 1)} for m, r, d in pattern]


TA = START + 7 * H   # a) DDoS claim on the police website, stayed up -> refuted
cyber_post(HACK, TA, "Faqja e Policisë së Kosovës është poshtë! #DDoS", "Kosovo Police website is down! #DDoS", "sq", "cy3_police", "telegram", "low")
cyber_events.append({"ext_id": "hold3-cyber-1", "kind": "ddos_claim", "title": "Hacktivists claim DDoS on the Kosovo Police website", "target": "Kosovo Police website",
    "sector": "government", "claimed_actor": "holdout3_hacktivist" + SYN, "source_name": "t.me/holdout3_hacktivist" + SYN, "source_type": "hacktivist",
    "source_url": "https://example.org/holdout3/telegram/1", "claimed_at": TA, "first_seen": TA + 2 * M,
    "raw": {"synthetic": True, "domain": "police.example.org", "keywords": ["policia", "police", "policija"], "gt_status": "refuted",
            "recorded_checks": probes(TA, [(3, "contradicts", "Homepage up (HTTP 200, 0.3 s)"), (15, "contradicts", "Homepage up (HTTP 200, 0.4 s)"),
                                           (40, "contradicts", "Homepage up (HTTP 200, 0.3 s)")], "https://police.example.org/")}})
cyber_events.append({"ext_id": "hold3-cyber-2", "kind": "outage", "title": "Kosovo Customs IT system outage", "target": "Kosovo Customs IT system (dogana)" + SYN,   # b) hybrid
    "sector": "government", "claimed_actor": None, "source_name": "Customs status page (recorded)" + SYN, "source_type": "media",
    "source_url": "https://example.org/holdout3/status/customs", "claimed_at": TE, "first_seen": TE + 5 * M,
    "raw": {"synthetic": True, "domain": "dogana.example.org", "keywords": ["dogan", "carin", "customs"], "gt_status": "confirmed", "gt_hybrid": "customs_hack_claim",
            "recorded_checks": probes(TE, [(5, "supports", "Customs declaration service not reachable (HTTP 503)"), (30, "supports", "Still not reachable (HTTP 503)")],
                                      "https://dogana.example.org/")}})
official(make_account("Kosovo Customs (holdout3)", START - 3000 * DAY, 30000, "sq"), TE + 50 * M,
         "Dogana: problem teknik në sistemin e deklarimit. Nuk ka asnjë sulm, punojmë për rikthimin.",
         "Customs: technical problem in the declaration system. There was no attack, we are working to restore it.", "customs_outage")
TC = START + 19 * H  # c) KEV Exchange -> confirmed
cyber_events.append({"ext_id": "hold3-cyber-3", "kind": "vulnerability", "title": "CVE-2021-26855 (Microsoft Exchange ProxyLogon) actively exploited",
    "target": "Microsoft Exchange Server", "sector": "other", "claimed_actor": None, "source_name": "CISA KEV (recorded)" + SYN, "source_type": "kev",
    "source_url": "https://www.cisa.gov/known-exploited-vulnerabilities-catalog", "claimed_at": TC, "first_seen": TC,
    "raw": {"synthetic": True, "cve": "CVE-2021-26855", "vendor": "Microsoft", "product": "Exchange Server", "gt_status": "confirmed",
            "recorded_checks": [{"check_name": "kev", "result": "supports", "checked_at": round(TC, 1), "detail": "CVE-2021-26855 listed as actively exploited",
                                 "evidence_url": "https://nvd.nist.gov/vuln/detail/CVE-2021-26855"}]}})
TD = START + 24 * H  # d) new ransomware listing of a private college, confirmed by statement + news
cyber_post(OUTS[0], TD + 3 * H, "Kolegji Demo në Prishtinë konfirmon sulm me ransomware, mësimi online i ndërprerë.",
           "Demo College in Pristina confirms a ransomware attack, online classes interrupted.", "sq", "cy3_college", "news", "credible")
cyber_post(make_account("Demo College (holdout3)", START - 2000 * DAY, 8000, "sq"), TD + 4 * H, "Demo College: sistemet tona u goditën nga ransomware. Po punojmë me policinë.",
           "Demo College: our systems were hit by ransomware. We are working with the police.", "sq", "cy3_college", "official", "credible")
cyber_events.append({"ext_id": "hold3-cyber-4", "kind": "ransomware_claim", "title": "Ransomware listing: Demo College, Pristina", "target": "Demo College" + SYN,
    "sector": "other", "claimed_actor": "DemoLocker3" + SYN, "source_name": "Ransomware tracker (recorded)" + SYN, "source_type": "ransomware_tracker",
    "source_url": "https://example.org/holdout3/ransomware/college", "claimed_at": TD, "first_seen": TD + 8 * M,
    "raw": {"synthetic": True, "country": "XK", "description": "Private college in Pristina, Kosovo", "keywords": ["demo college", "kolegji demo"], "gt_status": "confirmed"}})
TF = START + 37 * H  # e) telecom data-breach claim that re-posts a 2022 leak -> refuted
cyber_post(HACK, TF, "Kemi bazën e klientëve të Demo Telekom! #leak", "We have Demo Telekom's customer database!", "sq", "cy3_telecom", "telegram", "low")
cyber_events.append({"ext_id": "hold3-cyber-5", "kind": "data_breach_claim", "title": "Hacktivists claim a Demo Telekom customer-data leak", "target": "Demo Telekom" + SYN,
    "sector": "telecom", "claimed_actor": "holdout3_hacktivist" + SYN, "source_name": "t.me/holdout3_hacktivist" + SYN, "source_type": "hacktivist",
    "source_url": "https://example.org/holdout3/telegram/2", "claimed_at": TF, "first_seen": TF + 3 * M,
    "raw": {"synthetic": True, "country": "XK", "keywords": ["demo telekom"], "previously_listed_at": "12 April 2022",
            "previous_listing_url": "https://example.org/holdout3/leaks/2022", "gt_status": "refuted"}})
TG = START + 42 * H  # f) defacement of the Obiliq municipality site (site stays up, content changed) -> confirmed
cyber_post(HACK, TG, "Hakuam faqen e komunës së Obiliqit! #defaced", "We hacked the Obiliq municipality website!", "sq", "cy3_obiliq", "telegram", "low")
cyber_post(OUTS[3], TG + 45 * M, "Faqja e komunës së Obiliqit u hakua, u shfaq një mesazh i hakerëve.", "The Obiliq municipality website was hacked, a hacker message appeared.",
           "sq", "cy3_obiliq", "news", "credible")
cyber_events.append({"ext_id": "hold3-cyber-6", "kind": "defacement", "title": "Hacktivists claim defacement of the Obiliq municipality website",
    "target": "Obiliq municipality website", "sector": "government", "claimed_actor": "holdout3_hacktivist" + SYN, "source_name": "t.me/holdout3_hacktivist" + SYN,
    "source_type": "hacktivist", "source_url": "https://example.org/holdout3/telegram/3", "claimed_at": TG, "first_seen": TG + 3 * M,
    "raw": {"synthetic": True, "domain": "obiliq.example.org", "keywords": ["obiliq", "obilić"], "gt_status": "confirmed",
            "recorded_checks": probes(TG, [(5, "contradicts", "Homepage up (HTTP 200)"), (30, "contradicts", "Homepage up (HTTP 200)")], "https://obiliq.example.org/")
            + probes(TG, [(5, "supports", "Homepage content changed: hacker banner")], "https://obiliq.example.org/", name="content_hash")}})

# ---------------- write
OUT.mkdir(parents=True, exist_ok=True)
posts.sort(key=lambda p: p["ts"])
with open(OUT / "posts.jsonl", "w", encoding="utf-8") as f:
    for p in posts:
        f.write(json.dumps(p, ensure_ascii=False) + "\n")
with open(OUT / "cyber.json", "w", encoding="utf-8") as f:
    json.dump(cyber_events, f, ensure_ascii=False, indent=1)
factchecks = [
    {"id": "hold3-fc-1", "source": "HoldCheck (synthetic fact-checker)", "verdict": "False", "date": "2025-03-24", "url": "https://example.org/holdout3/factcheck/nato",
     "claim": "Claim that NATO is preparing to bomb Serbia from Kosovo. NATO po përgatit bombardim të Serbisë. NATO sprema bombardovanje Srbije."},
    {"id": "hold3-fc-2", "source": "HoldCheck (synthetic fact-checker)", "verdict": "False", "date": "2025-01-11", "url": "https://example.org/holdout3/factcheck/border",
     "claim": "Claim that Kosovo will close all crossings with Serbia. Kosova mbyll kufirin. Kosovo zatvara granicu."},
    {"id": "hold3-fc-3", "source": "HoldCheck (synthetic fact-checker)", "verdict": "False", "date": "2024-09-09", "url": "https://example.org/holdout3/factcheck/conscription",
     "claim": "Claim that military service becomes mandatory for all 18-year-old men. Shërbimi ushtarak i detyrueshëm. Obavezan vojni rok."},
    {"id": "hold3-fc-4", "source": "HoldCheck (synthetic fact-checker)", "verdict": "False", "date": "2025-11-02", "url": "https://example.org/holdout3/factcheck/schools",
     "claim": "Claim that schools will close for two weeks because of a virus. Shkollat mbyllen për shkak të virusit. Škole se zatvaraju zbog virusa."},
]
with open(OUT / "factchecks.json", "w", encoding="utf-8") as f:
    json.dump(factchecks, f, ensure_ascii=False, indent=1)
from collections import Counter
print(f"{len(posts)} posts, {len(accounts)} accounts, {len(cyber_events)} cyber claims")
print(Counter(p["gt_narrative"] for p in posts))
