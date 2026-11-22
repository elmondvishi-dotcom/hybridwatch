"""Evaluate HybridWatch on a HELD-OUT test set with the normal config: only the fact-check library and the
ground truth point at the held-out data.

    py scripts/make_holdout_data.py   &&  py scripts/evaluate_holdout.py            (test 1)
    py scripts/make_holdout2_data.py  &&  py scripts/evaluate_holdout.py --set 2    (test 2)
    py scripts/make_holdout3_data.py  &&  py scripts/evaluate_holdout.py --set 3    (test 3)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate  # noqa: E402

SETS = {
    "1": ("holdout", {"school_vaccine", "police_north", "bank_run"}, {
        "water_cut_gjilan": ("infrastructure", "Gjilan"), "flood_prizren": ("infrastructure", "Prizren"),
        "bank_outage": ("infrastructure", None),
        "school_vaccine": ("disinformation", None), "police_north": ("disinformation", None),
        "bank_run": ("disinformation", None), "remittance_tax": ("disinformation", None),
        "shots_zubin_potok": ("security_incident", "Zubin Potok"),
        "heroin_podujeva": ("organized_activity", "Podujeva"), "gracanica_protest": ("organized_activity", "Gračanica"),
        "rs_institutions": ("regional", None),
        "black_friday": (None, None), "rent_prices": (None, None)}),
    "2": ("holdout2", {"eulex_spy", "dam_collapse", "telecom_attack", "ballots_gjakova"}, {
        "heating_pristina": ("infrastructure", "Pristina"), "landslide_peja": ("infrastructure", "Peja"),
        "power_mitrovica": ("infrastructure", "Mitrovica"), "telecom_outage": ("infrastructure", None),
        "eulex_spy": ("disinformation", None), "dam_collapse": ("disinformation", None),
        "telecom_attack": ("disinformation", None), "ballots_gjakova": ("disinformation", None),
        "sugar_rumour": ("disinformation", None),
        "leposavic_device": ("security_incident", "Leposavić"), "ferizaj_grenade": ("security_incident", "Ferizaj"),
        "kacanik_smuggling": ("organized_activity", "Kaçanik"), "lipjan_tractors": ("organized_activity", "Lipjan"),
        "serbia_exercise": ("regional", None), "macedonia_border": ("regional", None),
        "marathon": (None, None), "exam_results": (None, None), "car_prices": (None, None)}),
    "3": ("holdout3", {"nato_bombing", "expulsion_claim", "customs_hack_claim"}, {
        "water_vushtrri": ("infrastructure", "Vushtrri"), "bridge_mitrovica": ("infrastructure", "Mitrovica"),
        "snow_dragash": ("infrastructure", "Dragash"), "train_fushe": ("infrastructure", "Fushë Kosova"),
        "gas_pristina": ("infrastructure", "Pristina"), "snow_kacanik": ("infrastructure", "Kaçanik"),
        "customs_outage": ("infrastructure", None),
        "shooting_obiliq": ("security_incident", "Obiliq"), "explosive_skenderaj": ("security_incident", "Skenderaj"),
        "arson_istog": ("security_incident", "Istog"), "armed_banjska": ("security_incident", "Banjska"),
        "protest_mitrovica": ("organized_activity", "Mitrovica"), "fuel_jarinje": ("organized_activity", "Jarinje crossing"),
        "teachers_gjakova": ("organized_activity", "Gjakova"), "drugs_fushe": ("organized_activity", "Fushë Kosova"),
        "weapons_drenas": ("organized_activity", "Drenas"),
        "serbia_trade": ("regional", None), "rs_police": ("regional", None), "serbia_alert": ("regional", None),
        "conscription_rumour": ("disinformation", None), "school_virus_rumour": ("disinformation", None),
        "nato_bombing": ("disinformation", None), "expulsion_claim": ("disinformation", None),
        "customs_hack_claim": ("disinformation", None),
        "eurovision": (None, None), "mall_opening": (None, None), "coffee_prices": (None, None)}),
}
args = sys.argv[1:]
which = "1"
if "--set" in args:
    i = args.index("--set")
    which = args[i + 1]
    del args[i:i + 2]
folder, evaluate.CAMPAIGNS, evaluate.EXPECTED_EVENTS = SETS[which]
HOLD = Path(__file__).resolve().parent.parent / "data" / folder
_load = evaluate.load_config


def load_holdout_config():
    cfg = _load()
    cfg["factcheckers"] = [{"name": "Held-out fact-check library (synthetic)", "type": "file",
                            "url": str(HOLD / "factchecks.json"), "enabled": True}]
    return cfg


evaluate.load_config = load_holdout_config
if __name__ == "__main__":
    sys.argv = [sys.argv[0], "--data", str(HOLD / "posts.jsonl"), "--cyber", str(HOLD / "cyber.json")] + args
    evaluate.main()
