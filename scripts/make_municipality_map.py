"""Build static/geo/kosovo-municipalities.geojson from the geoBoundaries ADM2 file for Kosovo.

    1. download (once, by hand):
       https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/XKX/ADM2/geoBoundaries-XKX-ADM2_simplified.geojson
    2. py scripts/make_municipality_map.py path/to/geoBoundaries-XKX-ADM2_simplified.geojson

Keeps the geometry (rounded to 4 decimals, ~10 m), replaces the source names (their ë / ç are broken in the
release when read as anything but UTF-8) with our ids and names from hybridwatch/places.py, and adds a label point per municipality.
Licence and attribution: static/geo/LICENSE.txt (CC BY-SA 2.0, data from OpenStreetMap via geoBoundaries).
"""
import json
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from hybridwatch.places import MUNICIPALITIES  # noqa: E402

OUT = ROOT / "hybridwatch" / "static" / "geo" / "kosovo-municipalities.geojson"

# geoBoundaries shapeName (after "Municipality of ", ASCII only, broken characters dropped) -> our id
SOURCE_NAME = {
    "partesh": "partesh", "dragash": "dragash", "rahovec": "rahovec", "shtrpc": "shterpce", "kamenica": "kamenica",
    "fush kosov": "fushe_kosova", "novobrd": "novo_brdo", "istog": "istog", "prizren": "prizren",
    "pristina": "prishtina", "han i elezit": "hani_elezit", "north mitrovica": "mitrovica_north",
    "kllokot": "klokot", "ferizaj": "ferizaj", "drenas": "drenas", "dean": "decan", "mitrovica": "mitrovica_south",
    "podujeva": "podujeva", "suhareka": "suhareka", "kaanik": "kacanik", "ranillug": "ranilug",
    "malisheva": "malisheva", "peja": "peja", "zubin potok": "zubin_potok", "junik": "junik", "viti": "viti",
    "gjakova": "gjakova", "zvean": "zvecan", "klina": "klina", "gjilan": "gjilan", "leposaviq": "leposavic",
    "gracanica": "gracanica", "mamusha": "mamusha", "vushtrri": "vushtrri", "lipjan": "lipjan",
    "skenderaj": "skenderaj", "obiliq": "obiliq", "shtime": "shtime",
    # the same names when the file is read with its diacritics intact
    "shterpce": "shterpce", "fushe kosove": "fushe_kosova", "novoberde": "novo_brdo", "decan": "decan",
    "kacanik": "kacanik", "zvecan": "zvecan",
}


def ascii_key(name: str) -> str:
    name = name.replace("Municipality of ", "")
    name = unicodedata.normalize("NFKD", name)
    return "".join(ch for ch in name if ch.isascii() and (ch.isalnum() or ch == " ")).lower().strip()


def rnd(coords):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], 4), round(coords[1], 4)]
    return [rnd(c) for c in coords]


def ring_area_centroid(ring):
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        f = x0 * y1 - x1 * y0
        a += f
        cx += (x0 + x1) * f
        cy += (y0 + y1) * f
    if abs(a) < 1e-12:
        return 0.0, ring[0]
    return abs(a / 2), [round(cx / (3 * a), 4), round(cy / (3 * a), 4)]


def main(src: str):
    g = json.loads(Path(src).read_text(encoding="utf-8", errors="replace"))
    names = {i: (sq, sr, cy) for i, sq, sr, cy, *_ in MUNICIPALITIES}
    feats, seen = [], set()
    for f in g["features"]:
        key = ascii_key(f["properties"]["shapeName"])
        mid = SOURCE_NAME.get(key)
        if not mid:
            raise SystemExit(f"Unknown municipality in source: {f['properties']['shapeName']!r} ({key})")
        seen.add(mid)
        geom = f["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        label = max((ring_area_centroid(p[0]) for p in polys), key=lambda x: x[0])[1]
        sq, sr, cy = names[mid]
        feats.append({"type": "Feature", "id": mid,
                      "properties": {"id": mid, "name": sq, "name_sr": sr, "name_sr_cyrl": cy, "label": label,
                                     "source_id": f["properties"].get("shapeID")},
                      "geometry": {"type": geom["type"], "coordinates": rnd(geom["coordinates"])}})
    missing = set(names) - seen
    if missing:
        raise SystemExit(f"Missing in source: {sorted(missing)}")
    out = {"type": "FeatureCollection",
           "attribution": "Municipal boundaries: geoBoundaries (gbOpen XKX ADM2, build Dec 2023), data © OpenStreetMap "
                          "contributors, CC BY-SA 2.0. Names: HybridWatch. See LICENSE.txt.",
           "features": sorted(feats, key=lambda f: f["id"])}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(feats)} municipalities)")


if __name__ == "__main__":
    main(sys.argv[1])
