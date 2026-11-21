"""Kosovo's 38 municipalities: names in Albanian, Serbian (Latin) and Serbian (Cyrillic), and detection of the
municipality a post talks about.

Matching runs on normalised text (lower case, no diacritics, Cyrillic -> Latin, see text.normalize), so one Latin
stem covers both Serbian scripts and the Albanian spelling without ë / ç. Stem rules as everywhere else
(threat.StemSet): 'stem' = word prefix, 'stem$' = whole word, 'two words' = phrase.

Some names are also ordinary words: Peć = stove, Istok = east, Viti = the year (Albanian), Klina = wedge.
For those only the unambiguous forms are stems; the bare name counts only when written with a capital letter
in the original post (RAW patterns, case-sensitive), and "Bliski Istok" (Middle East) never counts.

Border crossings and villages that are clearly inside one municipality map to it (Jarinje -> Leposavić,
Merdare -> Podujevë, Banjska -> Zvečan). A bare "Mitrovica" goes to North Mitrovica when the post also says
north / veri / sever, otherwise to (South) Mitrovica, the municipality that carries the plain name.

The map shapes are in static/geo/kosovo-municipalities.geojson (geoBoundaries, see static/geo/LICENSE.txt);
its feature ids are the ids below.
"""
from __future__ import annotations

import re

from .text import normalize
from .threat import StemSet

# id, Albanian, Serbian (Latin), Serbian (Cyrillic), stems (normalised text), raw case-sensitive regex or None
MUNICIPALITIES = [
    ("prishtina", "Prishtinë", "Priština", "Приштина", ["prishtin", "pristin"], None),
    ("mitrovica_south", "Mitrovicë e Jugut", "Južna Mitrovica", "Јужна Митровица",
     ["mitrovica e jugut", "mitrovice e jugut", "mitrovices se jugut", "juzna mitrovic", "juznoj mitrovic",
      "south mitrovic", "southern mitrovic"], None),
    ("mitrovica_north", "Mitrovicë e Veriut", "Severna Mitrovica", "Северна Митровица",
     ["mitrovica e veriut", "mitrovice e veriut", "mitrovices se veriut", "severna mitrovic", "severnoj mitrovic",
      "severnu mitrovic", "sjeverna mitrovic", "north mitrovic", "northern mitrovic"], None),
    ("zvecan", "Zveçan", "Zvečan", "Звечан", ["zvecan", "zveqan", "banjsk"], None),
    ("zubin_potok", "Zubin Potok", "Zubin Potok", "Зубин Поток", ["zubin potok", "zubinpotok", "zubinom potok",
                                                                   "zubin potoku"], None),
    ("leposavic", "Leposaviq", "Leposavić", "Лепосавић", ["leposavi", "jarinj"], None),
    ("vushtrri", "Vushtrri", "Vučitrn", "Вучитрн", ["vushtrri", "vucitrn"], None),
    ("skenderaj", "Skenderaj", "Srbica", "Србица", ["skenderaj", "srbica$", "srbici$", "srbicu$", "srbice$"], None),
    ("drenas", "Drenas (Gllogoc)", "Glogovac", "Глоговац", ["drenas", "gllogoc", "glogovac", "glogovc"], None),
    ("obiliq", "Obiliq", "Obilić", "Обилић", ["obiliq", "obilic"], None),
    ("fushe_kosova", "Fushë Kosovë", "Kosovo Polje", "Косово Поље", ["fushe kosov", "kosovo polj", "kosovu polj",
                                                                     "kosova polj"], None),
    ("podujeva", "Podujevë", "Podujevo", "Подујево", ["podujev", "merdar"], None),
    ("lipjan", "Lipjan", "Lipljan", "Липљан", ["lipjan", "lipljan"], None),
    ("gracanica", "Graçanicë", "Gračanica", "Грачаница", ["gracanic", "graqanic"], None),
    ("shtime", "Shtime", "Štimlje", "Штимље", ["shtime$", "shtimes$", "shtimen$", "stimlj"], None),
    ("novo_brdo", "Novobërdë", "Novo Brdo", "Ново Брдо", ["novoberd", "novo brd", "novom brd"], None),
    ("gjilan", "Gjilan", "Gnjilane", "Гњилане", ["gjilan", "gnjilan"], None),
    ("kamenica", "Kamenicë", "Kamenica", "Каменица", ["kamenic"], None),
    ("ranilug", "Ranillug", "Ranilug", "Ранилуг", ["ranillug", "ranilug"], None),
    ("partesh", "Partesh", "Parteš", "Партеш", ["partesh", "partes$", "partesu$"], None),
    ("klokot", "Kllokot", "Klokot", "Клокот", ["kllokot", "klokot"], None),
    ("viti", "Viti", "Vitina", "Витина", ["vitina$", "vitini$", "vitinu$", "vitine$", "komuna e vitise",
                                           "komuna viti$", "municipality of viti"], None),
    ("ferizaj", "Ferizaj", "Uroševac", "Урошевац", ["ferizaj", "urosevac", "urosevc"], None),
    ("kacanik", "Kaçanik", "Kačanik", "Качаник", ["kacanik", "kaqanik"], None),
    ("hani_elezit", "Hani i Elezit", "Đeneral Janković", "Ђенерал Јанковић",
     ["hani i elezit", "han i elezit", "elez han", "djeneral jankovic", "general jankovic"], None),
    ("shterpce", "Shtërpcë", "Štrpce", "Штрпце", ["shterpc", "strpc"], None),
    ("suhareka", "Suharekë", "Suva Reka", "Сува Река", ["suharek", "suva rek", "suvoj rec", "suvu rek"], None),
    ("prizren", "Prizren", "Prizren", "Призрен", ["prizren"], None),
    ("dragash", "Dragash", "Dragaš", "Драгаш", ["dragash", "dragas$", "dragasu$", "dragasa$"], None),
    ("mamusha", "Mamushë", "Mamuša", "Мамуша", ["mamush", "mamusa$", "mamusi$", "mamusu$"], None),
    ("rahovec", "Rahovec", "Orahovac", "Ораховац", ["rahovec", "rahovc", "orahovac", "orahovc"], None),
    ("malisheva", "Malishevë", "Mališevo", "Малишево", ["malishev", "malisev"], None),
    ("klina", "Klinë", "Klina", "Клина", ["kline$", "klines$", "klinen$"], r"\b(?:Klina|Klini|Klinu|Клина|Клини|Клину)\b"),
    ("gjakova", "Gjakovë", "Đakovica", "Ђаковица", ["gjakov", "djakovic", "dakovic"], None),
    ("decan", "Deçan", "Dečani", "Дечани", ["decan", "deqan"], None),
    ("junik", "Junik", "Junik", "Јуник", ["junik"], None),
    ("peja", "Pejë", "Peć", "Пећ", ["peja$", "peje$", "pejes$", "pejen$", "pecka$", "pecki$", "pecke$",
                                    "opstina pec$", "grad pec$"],
     r"\b(?:Peć|Peći|Пећ|Пећи)\b"),
    ("istog", "Istog", "Istok", "Исток", ["istog"],
     r"(?<!Bliski )(?<!Daleki )(?<!Близки )(?<!Далеки )\b(?:Istok|Istoka|Istoku|Исток|Истока|Истоку)\b"),
]

# bare "Mitrovica" (any form) and the words that send it north
_MITROVICA = StemSet(["mitrovic"])
_NORTH = StemSet(["veri$", "veriut$", "verior", "sever", "north"])


class Municipalities:
    def __init__(self):
        self.items = [{"id": i, "name": sq, "name_sr": sr, "name_sr_cyrl": cy} for i, sq, sr, cy, _, _ in MUNICIPALITIES]
        self.by_id = {m["id"]: m for m in self.items}
        self._stems = [(i, StemSet(stems)) for i, _, _, _, stems, _ in MUNICIPALITIES]
        self._raw = [(i, re.compile(rx)) for i, *_, rx in MUNICIPALITIES if rx]

    def label(self, mid: str) -> str:
        m = self.by_id.get(mid)
        return f"{m['name']} / {m['name_sr']}" if m and m["name"] != m["name_sr"] else (m["name"] if m else mid)

    def find(self, text: str, n: str | None = None) -> set[str]:
        """Municipality ids named in one post (raw text; n = its normalised form if already computed)."""
        n = normalize(text) if n is None else n
        out = {i for i, s in self._stems if s.search(n)}
        out |= {i for i, rx in self._raw if rx.search(text)}
        if _MITROVICA.search(n) and not out & {"mitrovica_north", "mitrovica_south"}:
            out.add("mitrovica_north" if _NORTH.search(n) else "mitrovica_south")
        return out


_INSTANCE: Municipalities | None = None


def municipalities() -> Municipalities:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = Municipalities()
    return _INSTANCE
