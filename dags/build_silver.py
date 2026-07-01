"""
build_silver.py
----------------
Construit la couche Silver enterprise_silver depuis enterprises (Bronze, intact) :
  - dates normalisees (DD-MM-YYYY -> YYYY-MM-DD)
  - activites dedupliquees (NaceCode + Classification exacts) + labels FR
  - adresse unique (TypeOfAddress = REGO, relue depuis address.csv)
  - denomination principale (TypeOfDenomination = 001) + secondaires (relues depuis denomination.csv)
  - labels FR pour JuridicalForm et Status

enterprises n'est jamais modifiee : ce script ne fait que lire dedans et ecrire dans
enterprise_silver.
"""

import argparse
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from pymongo import UpdateOne

from db.mongo_client import get_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_DEFAULT_KBO = Path(__file__).resolve().parent.parent / "data" / "kbo"
DEFAULT_KBO_PATH = os.getenv("KBO_PATH", str(_DEFAULT_KBO))

BATCH_SIZE = 5_000
CHUNK_SIZE = 200_000

NACE_CATEGORY_BY_VERSION = {
    "2003": "Nace2003",
    "2008": "Nace2008",
    "2025": "Nace2025",
}


def _load_labels(kbo_path: Path, category: str) -> dict:
    """Charge code.csv filtre sur une categorie (Language=FR) -> dict code -> label."""
    df = pd.read_csv(kbo_path / "code.csv", dtype=str)
    sub = df[(df["Category"] == category) & (df["Language"] == "FR")]
    return dict(zip(sub["Code"], sub["Description"]))


def _load_nace_labels(kbo_path: Path) -> dict:
    """dict {nace_version: {code: label}} pour Nace2003/2008/2025."""
    return {
        version: _load_labels(kbo_path, category)
        for version, category in NACE_CATEGORY_BY_VERSION.items()
    }


def _load_rego_addresses(kbo_path: Path, known_nums: set) -> dict:
    """
    Relit address.csv par chunks, ne garde que TypeOfAddress=REGO et non radiees
    (DateStrikingOff vide) -> dict {enterprise_number: {...}}.
    """
    addresses: dict = {}
    for chunk in pd.read_csv(kbo_path / "address.csv", dtype=str, chunksize=CHUNK_SIZE):
        rego = chunk[
            (chunk["TypeOfAddress"] == "REGO")
            & (chunk["DateStrikingOff"].isna())
            & chunk["EntityNumber"].isin(known_nums)
        ]
        for _, row in rego.iterrows():
            addresses[row["EntityNumber"]] = {
                "zip":          row.get("Zipcode"),
                "city_fr":      row.get("MunicipalityFR"),
                "city_nl":      row.get("MunicipalityNL"),
                "street_fr":    row.get("StreetFR"),
                "house_number": row.get("HouseNumber"),
            }
    log.info(f"  address.csv : {len(addresses):,} adresses REGO trouvees")
    return addresses


def _load_denominations(kbo_path: Path, known_nums: set) -> dict:
    """
    Relit denomination.csv par chunks -> dict {enterprise_number: {"name": ..., "secondary_names": [...]}}.
    Nom principal = TypeOfDenomination 001, prefere Language 1 (FR) puis 2 (NL).
    """
    by_enterprise: dict = {}
    for chunk in pd.read_csv(kbo_path / "denomination.csv", dtype=str, chunksize=CHUNK_SIZE):
        sub = chunk[chunk["EntityNumber"].isin(known_nums)]
        for num, grp in sub.groupby("EntityNumber"):
            entry = by_enterprise.setdefault(num, {"name": None, "_name_lang": None, "secondary_names": []})
            for _, row in grp.iterrows():
                denom = row["Denomination"]
                if row["TypeOfDenomination"] == "001":
                    lang = row["Language"]
                    if entry["name"] is None or (lang < entry["_name_lang"]):
                        if entry["name"] is not None:
                            entry["secondary_names"].append(entry["name"])
                        entry["name"] = denom
                        entry["_name_lang"] = lang
                    else:
                        entry["secondary_names"].append(denom)
                else:
                    entry["secondary_names"].append(denom)

    for entry in by_enterprise.values():
        entry.pop("_name_lang", None)

    log.info(f"  denomination.csv : {len(by_enterprise):,} entreprises avec denomination(s)")
    return by_enterprise


def _normalize_date(raw: str | None) -> str | None:
    if not raw or not isinstance(raw, str):
        return raw
    try:
        return datetime.strptime(raw, "%d-%m-%Y").strftime("%Y-%m-%d")
    except ValueError:
        return raw


def _dedupe_activities(activities: list[dict], nace_labels: dict) -> list[dict]:
    seen = {}
    for act in activities:
        key = (act.get("nace_code"), act.get("classification"))
        if key in seen:
            continue
        version = act.get("nace_version", "")
        label = nace_labels.get(version, {}).get(act.get("nace_code"), "")
        seen[key] = {**act, "nace_label": label}
    return list(seen.values())


def build_silver(kbo_path: str = DEFAULT_KBO_PATH) -> None:
    kbo = Path(kbo_path)
    db = get_db()

    log.info("=" * 60)
    log.info("BUILD SILVER — enterprise_silver")
    log.info("=" * 60)

    log.info("Chargement des labels (code.csv)...")
    juridical_form_labels = _load_labels(kbo, "JuridicalForm")
    status_labels = _load_labels(kbo, "Status")
    nace_labels = _load_nace_labels(kbo)

    known_nums = {
        doc["enterprise_number"] for doc in db.enterprises.find({}, {"enterprise_number": 1})
    }
    log.info(f"  {len(known_nums):,} entreprises dans enterprises (Bronze)")

    log.info("Rechargement adresses REGO et denominations depuis les CSV bruts...")
    rego_addresses = _load_rego_addresses(kbo, known_nums)
    denominations = _load_denominations(kbo, known_nums)

    log.info("Construction de enterprise_silver...")
    ops = []
    count = 0
    address_fallback = 0
    now = datetime.now(timezone.utc)

    for doc in db.enterprises.find({}):
        num = doc["enterprise_number"]

        address = rego_addresses.get(num)
        if address is None:
            address_fallback += 1
            address = {
                "zip":          doc.get("zip"),
                "city_fr":      doc.get("city_fr"),
                "city_nl":      doc.get("city_nl"),
                "street_fr":    doc.get("street_fr"),
                "house_number": doc.get("house_number"),
            }

        denom = denominations.get(num, {"name": doc.get("name"), "secondary_names": []})

        legal_form = doc.get("legal_form") or ""
        status = doc.get("status") or ""

        silver_doc = {
            "enterprise_number":  num,
            "status":             status,
            "status_label":       status_labels.get(status, ""),
            "legal_form":         legal_form,
            "legal_form_label":   juridical_form_labels.get(legal_form.zfill(3), ""),
            "start_date":         _normalize_date(doc.get("start_date")),
            "name":               denom.get("name"),
            "secondary_names":    denom.get("secondary_names", []),
            "address":            address,
            "activities":         _dedupe_activities(doc.get("activities", []), nace_labels),
            "establishments":     doc.get("establishments", []),
            "silver_updated_at":  now,
        }

        ops.append(UpdateOne({"enterprise_number": num}, {"$set": silver_doc}, upsert=True))
        count += 1

        if len(ops) >= BATCH_SIZE:
            db.enterprise_silver.bulk_write(ops, ordered=False)
            log.info(f"  Silver : {count:,} entreprises traitees")
            ops = []

    if ops:
        db.enterprise_silver.bulk_write(ops, ordered=False)

    db.enterprise_silver.create_index("enterprise_number", unique=True, name="idx_silver_enterprise_number")

    total = db.enterprise_silver.count_documents({})
    log.info("=" * 60)
    log.info(f"Termine — {total:,} entreprises dans enterprise_silver")
    log.info(f"  Adresses en repli sur le Bronze (pas d'adresse REGO trouvee) : {address_fallback:,}")
    log.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--kbo-path", default=DEFAULT_KBO_PATH)
    args = parser.parse_args()
    build_silver(args.kbo_path)
