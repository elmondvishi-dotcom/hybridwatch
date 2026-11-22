"""Generate a labelled TRUE / FALSE post dataset for the "how right is it?" experiment.

TRUE  = an accurate report of a real security event (police / officials / outlets / eyewitnesses)
FALSE = a false or fabricated claim (inflated casualties, invented attacks, fake poisoning, fake troop
        movements, recycled hoaxes), the kind fact-checkers debunk

To make the test honest, the test data is NOT drawn from the same pot as the training data:
  train : 1000 true + 1000 false, events E1..E12, wording style A
  t1    : unseen posts, SAME events, style A               (easy - shows memorisation)
  t2    : NEW events E13..E20, style A                     (does it generalise to new events?)
  t3    : NEW events E13..E20, wording style B             (new events + new way of writing: hardest)
  train_diverse : same size as train, half style A + half style C (for the "diverse data" experiment)
Both classes contain hard cases that look like the other class: true posts from panicked eyewitnesses
in caps, false posts written calmly with a fake "police confirmed".
Every post also has account metadata (age, followers, activity, copy-paste group size), with overlap
between the classes, because real disinformation also comes from old accounts.

    py scripts/truth_data.py          -> data/truth/{train,t1,t2,t3}.jsonl

SYNTHETIC: all events are fictional combinations of real place names.
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "truth"
rng = random.Random(2026)

# place: (sq, sr, en)
PLACES = [
    ("Mitrovicë", "Mitrovica", "Mitrovica"), ("Zveçan", "Zvečan", "Zvečan"), ("Leposaviq", "Leposavić", "Leposavić"),
    ("Zubin Potok", "Zubin Potok", "Zubin Potok"), ("Prishtinë", "Priština", "Pristina"), ("Ferizaj", "Uroševac", "Ferizaj"),
    ("Pejë", "Peć", "Peja"), ("Prizren", "Prizren", "Prizren"), ("Gjilan", "Gnjilane", "Gjilan"), ("Vushtrri", "Vučitrn", "Vushtrri"),
    ("Jarinjë", "Jarinje", "Jarinje"), ("Merdarë", "Merdare", "Merdare"), ("Graçanicë", "Gračanica", "Gračanica"),
    ("Podujevë", "Podujevo", "Podujeva"), ("Gjakovë", "Đakovica", "Gjakova"), ("Bërnjak", "Brnjak", "Brnjak"),
    ("Shtërpcë", "Štrpce", "Shtërpca"), ("Kamenicë", "Kamenica", "Kamenica"), ("Obiliq", "Obilić", "Obiliq"), ("Rahovec", "Orahovac", "Rahovec"),
]
KINDS = ["armed", "explosion", "blockade", "border", "cyber", "weapons", "arrest", "threat"]
# 20 events = place + kind; first 12 for training, last 8 held out
EVENTS = [(i, PLACES[i], KINDS[i % len(KINDS)]) for i in range(20)]
TRAIN_EVENTS, TEST_EVENTS = EVENTS[:12], EVENTS[12:]

L = {"sq": 0, "sr": 1, "en": 2}


def pick(x):
    return rng.choice(x)


# ------------------------------------------------------------------ wording style A
A = {
  "src": [("Policia e Kosovës njofton se", "Kosovska policija saopštila je da", "Kosovo Police said that"),
          ("Sipas policisë,", "Prema policiji,", "According to police,"),
          ("Ministria e Brendshme konfirmoi se", "Ministarstvo unutrašnjih poslova potvrdilo je da", "The Interior Ministry confirmed that"),
          ("KFOR-i njoftoi se", "KFOR je saopštio da", "KFOR said that")],
  "fact": {
    "armed": [("një patrullë policie u sulmua me armë zjarri pranë {p}; një polic u plagos lehtë",
               "policijska patrola napadnuta je vatrenim oružjem kod {p}; jedan policajac lakše je ranjen",
               "a police patrol was attacked with firearms near {p}; one officer was lightly wounded"),
              ("{m} sulmues u vranë në përleshjen me policinë pranë {p}",
               "{m} napadača ubijena su u sukobu sa policijom kod {p}",
               "{m} attackers were killed in a clash with police near {p}")],
    "explosion": [("një shpërthim dëmtoi një tub uji pranë {p}; furnizimi u ndërpre për disa orë",
                   "eksplozija je oštetila vodovodnu cev kod {p}; snabdevanje je prekinuto nekoliko sati",
                   "an explosion damaged a water pipe near {p}; supply was cut for several hours")],
    "blockade": [("disa dhjetëra persona bllokuan rrugën kryesore në {p} për tri orë",
                  "nekoliko desetina ljudi blokiralo je glavni put u {p} tri sata",
                  "several dozen people blocked the main road in {p} for three hours")],
    "border": [("pikëkalimi në {p} u mbyll përkohësisht për shkak të kontrollave të shtuara",
                "prelaz {p} privremeno je zatvoren zbog pojačanih kontrola",
                "the {p} crossing was temporarily closed because of increased checks")],
    "cyber": [("faqja e komunës së {p} u sulmua nga hakerët dhe nuk funksionoi gjithë ditën",
               "sajt opštine {p} napali su hakeri i nije radio ceo dan",
               "the {p} municipality website was hit by hackers and was down all day")],
    "weapons": [("u sekuestruan dy pushkë automatike dhe municion në një shtëpi në {p}",
                 "u kući u {p} zaplenjene su dve automatske puške i municija",
                 "two automatic rifles and ammunition were seized in a house in {p}")],
    "arrest": [("dy persona u arrestuan në {p} të dyshuar për sulmin ndaj patrullës",
                "u {p} uhapšene su dve osobe osumnjičene za napad na patrolu",
                "two people were arrested in {p} on suspicion of attacking the patrol")],
    "threat": [("një gazetar në {p} raportoi kërcënime me vdekje në rrjete sociale; policia ka nisur hetimet",
                "novinar iz {p} prijavio je pretnje smrću na društvenim mrežama; policija je pokrenula istragu",
                "a journalist in {p} reported death threats on social media; police have opened an investigation")],
  },
  "detail": [("Hetimet vazhdojnë.", "Istraga je u toku.", "The investigation is ongoing."),
             ("Nuk ka të lënduar civilë.", "Nema povređenih civila.", "No civilians were hurt."),
             ("Situata është nën kontroll.", "Situacija je pod kontrolom.", "The situation is under control."),
             ("Rruga u rihap në mbrëmje.", "Put je ponovo otvoren uveče.", "The road reopened in the evening.")],
  # eyewitness TRUE posts: emotional, no attribution (hard cases)
  "eye": [("Tani po dëgjohen të shtëna pranë {p}, policia kudo!!", "Upravo se čuje pucnjava kod {p}, policija svuda!!",
           "Shots being heard near {p} right now, police everywhere!!"),
          ("Ka shumë polici dhe ambulanca në {p}, diçka ka ndodhur", "Puno policije i hitne pomoći u {p}, nešto se desilo",
           "Lots of police and ambulances in {p}, something happened"),
          ("Rruga për {p} e bllokuar, kthehuni mbrapa", "Put za {p} blokiran, vratite se", "Road to {p} blocked, turn back")],
  # FALSE posts
  "hook": [("URGJENT!", "HITNO!", "URGENT!"), ("SHPËRNDAJENI PARA SE TA FSHIJNË!", "PODELITE PRE NEGO ŠTO OBRIŠU!",
            "SHARE BEFORE THEY DELETE IT!"), ("Mediat heshtin:", "Mediji ćute:", "The media are silent:"),
           ("Po na e fshehin:", "Kriju od nas:", "They are hiding it:")],
  "claim": [("{n} të vrarë në {p}, qeveria e fsheh numrin e vërtetë", "{n} mrtvih u {p}, vlast krije pravi broj",
             "{n} dead in {p}, the government hides the real number"),
            ("uji në {p} është helmuar, mos pini ujë nga rubineti", "voda u {p} je zatrovana, ne pijte vodu iz česme",
             "the water in {p} has been poisoned, don't drink tap water"),
            ("ushtria serbe hyri në {p} këtë natë", "srpska vojska je noćas ušla u {p}", "the Serbian army entered {p} tonight"),
            ("KFOR-i largohet nga {p} javën tjetër, askush s'do na mbrojë", "KFOR napušta {p} sledeće nedelje, niko nas neće štititi",
             "KFOR is leaving {p} next week, nobody will protect us"),
            ("ka bombë në shkollën në {p}, evakuoni fëmijët", "bomba u školi u {p}, evakuišite decu", "there is a bomb in the school in {p}, evacuate the children"),
            ("orë policore nga sonte në {p}", "policijski čas od večeras u {p}", "curfew in {p} from tonight")],
  "fakesrc": [("Burime të sigurta thonë kështu.", "Pouzdani izvori to kažu.", "Reliable sources say so."),
              ("Më tha një shok në polici.", "Rekao mi je drug iz policije.", "A friend in the police told me."),
              ("Insajderët e konfirmojnë.", "Insajderi potvrđuju.", "Insiders confirm it.")],
  # calm FALSE posts with a fake official attribution (hard cases)
  "calmfalse": [("Policia e Kosovës konfirmoi se {n} sulmues u vranë pranë {p} sot.", "Kosovska policija potvrdila je da je {n} napadača ubijeno kod {p} danas.",
                 "Kosovo Police confirmed that {n} attackers were killed near {p} today."),
                ("Sipas Ministrisë, uji në {p} nuk është i pijshëm deri në njoftimin e radhës.", "Prema Ministarstvu, voda u {p} nije za piće do daljeg.",
                 "According to the Ministry, water in {p} is not drinkable until further notice.")],
}

# ------------------------------------------------------------------ wording style B (different phrasing)
B = {
  "src": [("Në një deklaratë, policia tha se", "U saopštenju policije navodi se da", "In a statement, police said"),
          ("Autoritetet raportuan se", "Vlasti su objavile da", "Authorities reported that"),
          ("Zëdhënësi i policisë deklaroi se", "Portparol policije izjavio je da", "A police spokesperson stated that")],
  "fact": {
    "armed": [("persona të panjohur hapën zjarr ndaj një automjeti policor në {p}, pa viktima",
               "nepoznate osobe otvorile su vatru na policijsko vozilo u {p}, bez žrtava",
               "unknown persons opened fire on a police vehicle in {p}, with no casualties"),
              ("në shkëmbimin e zjarrit në {p} humbën jetën {m} persona të armatosur",
               "u razmeni vatre u {p} poginulo je {m} naoružanih osoba",
               "{m} armed men died in an exchange of fire in {p}")],
    "explosion": [("një mjet shpërthyes dëmtoi një trafo në {p}, disa lagje mbetën pa rrymë",
                   "eksplozivna naprava oštetila je trafo stanicu u {p}, nekoliko naselja bez struje",
                   "an explosive device damaged a transformer in {p}, leaving several neighbourhoods without power")],
    "blockade": [("protestuesit vendosën kamionë në rrugë në {p}; policia i largoi pas negociatave",
                  "demonstranti su postavili kamione na put u {p}; policija ih je uklonila posle pregovora",
                  "protesters parked trucks across the road in {p}; police removed them after talks")],
    "border": [("qarkullimi në kufi te {p} u pezullua për dy orë pas një incidenti",
                "saobraćaj na granici kod {p} obustavljen je dva sata posle incidenta",
                "traffic at the border near {p} was suspended for two hours after an incident")],
    "cyber": [("sistemi i regjistrimit të automjeteve në {p} u bllokua nga një sulm kibernetik",
               "sistem za registraciju vozila u {p} blokiran je sajber napadom",
               "the vehicle registration system in {p} was blocked by a cyber attack")],
    "weapons": [("gjatë një kontrolli në {p} u gjetën granata dore dhe eksploziv",
                 "tokom pretresa u {p} pronađene su ručne bombe i eksploziv",
                 "hand grenades and explosives were found during a search in {p}")],
    "arrest": [("një i dyshuar për shitje armësh u ndalua në {p}", "osumnjičeni za prodaju oružja priveden je u {p}",
                "a suspect in weapons trafficking was detained in {p}")],
    "threat": [("një aktivist në {p} mori mesazhe kërcënuese pas një proteste", "aktivista iz {p} dobio je preteće poruke posle protesta",
                "an activist in {p} received threatening messages after a protest")],
  },
  "detail": [("Rasti po hetohet nga prokuroria.", "Slučaj istražuje tužilaštvo.", "Prosecutors are investigating the case."),
             ("Nuk ka rrezik për qytetarët.", "Nema opasnosti za građane.", "There is no danger to citizens."),
             ("Më shumë informata do të jepen më vonë.", "Više informacija biće objavljeno kasnije.", "More information will follow later.")],
  "eye": [("Sapo pashë një kolonë policie që shkonte drejt {p}, ç'po ndodh??", "Upravo sam video kolonu policije ka {p}, šta se dešava??",
           "Just saw a police convoy heading to {p}, what's going on??"),
          ("Komshinjtë në {p} thonë se u dëgjua një shpërthim", "Komšije u {p} kažu da se čula eksplozija", "Neighbours in {p} say an explosion was heard")],
  "hook": [("ZGJOHUNI!", "PROBUDITE SE!", "WAKE UP!"), ("Askush s'do t'jua thotë këtë:", "Ovo vam niko neće reći:", "Nobody will tell you this:"),
           ("LAJM I FUNDIT!!!", "NAJNOVIJE!!!", "BREAKING!!!")],
  "claim": [("trupat po grumbullohen në {p}, lufta po fillon", "trupe se gomilaju kod {p}, rat počinje", "troops are massing near {p}, war is starting"),
            ("në {p} janë vrarë {n} civilë, por lajmet nuk e thonë", "u {p} je ubijeno {n} civila, ali vesti to ne govore",
             "{n} civilians have been killed in {p}, but the news won't say it"),
            ("bankat në {p} do të mbyllen nesër, tërhiqni paratë", "banke u {p} se zatvaraju sutra, podignite novac",
             "banks in {p} close tomorrow, withdraw your money"),
            ("ura në {p} u hodh në erë natën", "most u {p} je noćas dignut u vazduh", "the bridge in {p} was blown up overnight")],
  "fakesrc": [("Është konfirmuar nga njerëz brenda.", "Potvrđeno od ljudi iznutra.", "Confirmed by people on the inside."),
              ("E pashë në një grup privat.", "Video sam u privatnoj grupi.", "I saw it in a private group.")],
  "calmfalse": [("Zëdhënësi i policisë deklaroi se {n} persona humbën jetën në {p} mbrëmë.", "Portparol policije izjavio je da je {n} osoba poginulo u {p} sinoć.",
                 "A police spokesperson stated that {n} people died in {p} last night.")],
}

# ------------------------------------------------------------------ wording style C (a second TRAINING style,
# used only for the "diverse training data" experiment; never used in any test set)
C = {
  "src": [("Burimet zyrtare bëjnë të ditur se", "Zvanični izvori navode da", "Official sources say that"),
          ("Në konferencën për media u tha se", "Na konferenciji za medije rečeno je da", "A press conference was told that"),
          ("Raporti i policisë thotë se", "U policijskom izveštaju stoji da", "The police report says that")],
  "fact": {
    "armed": [("u qëllua ndaj një pike kontrolli policore në {p}; nuk ka të vdekur", "pucano je na policijski punkt u {p}; nema mrtvih",
               "shots were fired at a police checkpoint in {p}; nobody was killed"),
              ("{m} persona të armatosur u neutralizuan pas sulmit në {p}", "{m} naoružane osobe neutralisane su posle napada u {p}",
               "{m} armed individuals were neutralised after the attack in {p}")],
    "explosion": [("një shpërthim dëmtoi një linjë elektrike afër {p}", "eksplozija je oštetila dalekovod blizu {p}",
                   "a blast damaged a power line close to {p}")],
    "blockade": [("rruga drejt {p} u mbyll nga protestuesit deri në mesditë", "put ka {p} demonstranti su zatvorili do podneva",
                  "protesters closed the road to {p} until midday")],
    "border": [("kontrollet në pikën kufitare {p} u shtuan dhe radhët u zgjatën", "kontrole na prelazu {p} su pojačane i redovi su se produžili",
                "checks at the {p} crossing were stepped up and queues grew longer")],
    "cyber": [("sistemet e spitalit në {p} u goditën nga ransomware", "sistemi bolnice u {p} pogođeni su ransomverom",
               "hospital systems in {p} were hit by ransomware")],
    "weapons": [("në një automjet në {p} u gjetën armë pa leje", "u vozilu u {p} pronađeno je oružje bez dozvole",
                 "unlicensed weapons were found in a vehicle in {p}")],
    "arrest": [("një burrë u ndalua në {p} për posedim eksplozivi", "muškarac je priveden u {p} zbog posedovanja eksploziva",
                "a man was detained in {p} for possessing explosives")],
    "threat": [("një zyrtar komunal në {p} mori letra kërcënuese", "opštinski službenik u {p} dobio je preteća pisma",
                "a municipal official in {p} received threatening letters")],
  },
  "detail": [("Policia apelon për qetësi.", "Policija apeluje na mir.", "Police are calling for calm."),
             ("Zonën e ka siguruar policia.", "Policija je obezbedila područje.", "Police have secured the area.")],
  "eye": [("A e dëgjoi dikush atë zhurmë në {p}?? si shpërthim", "Da li je neko čuo onaj zvuk u {p}?? kao eksplozija",
           "Did anyone hear that noise in {p}?? like an explosion"),
          ("Policia e ka mbyllur qendrën e {p}, mos ejani", "Policija je zatvorila centar {p}, ne dolazite", "Police closed the centre of {p}, don't come")],
  "hook": [("KUJDES!!", "PAŽNJA!!", "ATTENTION!!"), ("Ja çfarë nuk po ju tregojnë:", "Evo šta vam ne govore:", "Here's what they're not telling you:"),
           ("Shikojeni para se të zhduket:", "Pogledajte pre nego što nestane:", "Watch before it disappears:")],
  "claim": [("në {p} ka mbi {n} të vdekur pas sulmit të mbrëmshëm", "u {p} ima preko {n} mrtvih posle sinoćnjeg napada",
             "over {n} people are dead in {p} after last night's attack"),
            ("tanket kanë hyrë në {p}", "tenkovi su ušli u {p}", "tanks have rolled into {p}"),
            ("qeveria do të ndërpresë internetin në {p} nesër", "vlada će sutra ugasiti internet u {p}", "the government will shut down the internet in {p} tomorrow"),
            ("dikush ka hedhur helm në ujin e {p}", "neko je bacio otrov u vodu u {p}", "someone has put poison in the water of {p}")],
  "fakesrc": [("Ma dërgoi një i afërm që punon atje.", "Poslao mi je rođak koji tamo radi.", "A relative who works there sent it to me."),
              ("Është në të gjitha grupet.", "Ima ga u svim grupama.", "It's in all the groups.")],
  "calmfalse": [("Raporti i policisë thotë se {n} persona u vranë në {p} dje.", "U policijskom izveštaju stoji da je {n} osoba ubijeno u {p} juče.",
                 "The police report says {n} people were killed in {p} yesterday.")],
}


def account(label: str, hard: bool) -> dict:
    """Account metadata, overlapping between classes (disinformation also comes from old accounts)."""
    if label == "true":
        new = rng.random() < (0.25 if hard else 0.08)
        outlet = (not hard) and rng.random() < 0.35
    else:
        new = rng.random() < (0.3 if hard else 0.6)
        outlet = False
    age = rng.uniform(2, 40) if new else rng.uniform(200, 4000)
    followers = int(math.exp(rng.gauss(10.5 if outlet else 6.0, 1.0)))
    posts_day = round(rng.lognormvariate(1.0 if label == "false" and not hard else 0.4, 0.8), 2)
    copy_group = 1
    if label == "false" and rng.random() < (0.25 if hard else 0.5):
        copy_group = rng.randint(3, 40)
    elif label == "true" and rng.random() < 0.1:
        copy_group = rng.randint(2, 8)     # outlets re-publishing an agency story
    return {"account_age_days": round(age, 1), "followers": followers, "posts_per_day": posts_day,
            "copy_group_size": copy_group, "is_outlet": outlet}


def make_post(style: dict, event, label: str) -> dict:
    _, place, kind = event
    lang = rng.choices(["sq", "sr", "en"], [0.45, 0.4, 0.15])[0]
    i = L[lang]
    p = place[i]
    n = rng.choice([7, 9, 12, 15, 20, 30])
    hard = rng.random() < 0.25
    if label == "true":
        if hard:
            text = pick(style["eye"])[i].format(p=p)
        else:
            fact = pick(style['fact'][kind])[i].format(p=p, m=rng.choice([1, 2, 3]))
            text = f"{pick(style['src'])[i]} {fact}. {pick(style['detail'])[i]}"
    else:
        if hard:
            text = pick(style["calmfalse"])[i].format(p=p, n=n)
        else:
            parts = [pick(style["hook"])[i], pick(style["claim"])[i].format(p=p, n=n)]
            if rng.random() < 0.6:
                parts.append(pick(style["fakesrc"])[i])
            text = " ".join(parts)
            if rng.random() < 0.3:
                text = text.upper()
    if rng.random() < 0.15:
        text += " " + pick(["🙏", "😡", "⚠️", "#Kosovo", "#Kosova", "!!", "?"])
    return {"text": text, "lang": lang, "label": label, "hard": hard, "event": f"E{event[0] + 1}",
            "place": place[2], "kind": kind, **account(label, hard)}


def build(style, events, n_each):
    rows = [make_post(style, pick(events), lab) for lab in ("true", "false") for _ in range(n_each)]
    rng.shuffle(rows)
    return rows


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sets = {
        "train": build(A, TRAIN_EVENTS, 1000),
        "t1": [],
        "t2": build(A, TEST_EVENTS, 300),
        "t3": build(B, TEST_EVENTS, 300),
    }
    # t1 must not contain exact training texts (otherwise it is not "unseen"); keep it balanced
    seen = {r["text"] for r in sets["train"]}
    pool = [r for r in build(A, TRAIN_EVENTS, 1500) if r["text"] not in seen]
    tr = [r for r in pool if r["label"] == "true"]
    fa = [r for r in pool if r["label"] == "false"]
    k = min(150, len(tr), len(fa))
    sets["t1"] = tr[:k] + fa[:k]
    rng.shuffle(sets["t1"])
    # diversity experiment: same total size as train, but half style A and half style C (training events only)
    sets["train_diverse"] = build(A, TRAIN_EVENTS, 500) + build(C, TRAIN_EVENTS, 500)
    rng.shuffle(sets["train_diverse"])
    for name, rows in sets.items():
        with open(OUT / f"{name}.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        t = sum(r["label"] == "true" for r in rows)
        print(f"{name:6s} {len(rows):5d} posts  ({t} true / {len(rows) - t} false, "
              f"{sum(r['hard'] for r in rows)} hard cases)")
    uniq = len({r['text'] for r in sets['train']})
    print(f"unique texts in train: {uniq} of {len(sets['train'])}")


if __name__ == "__main__":
    main()
