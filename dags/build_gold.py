"""
build_gold.py
--------------
Construit la couche Gold hotel_gold depuis les CSV CBSO stockes dans HDFS
(/data/bronze/{enterprise_number}/cbso/csvs/{annee}.csv).

La liste des fichiers a traiter vient de download_state (deja rempli par
ingestion_dag.py) : pas besoin de lister HDFS, on a deja enterprise_number/year/hdfs_path
pour chaque CSV telecharge avec succes.

Parsing et formules reprises de consult.py::parse_csv / compute_kpis (deja testees sur
de vrais CSV NBB).
"""

import argparse
import logging
import os
import re
from datetime import datetime, timezone
from io import StringIO

import pandas as pd
from hdfs import InsecureClient
from pymongo import UpdateOne

from db.mongo_client import get_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

HDFS_URL  = os.getenv("HDFS_URL", "http://localhost:9870")
HDFS_USER = os.getenv("HDFS_USER", "hdfs")

BATCH_SIZE = 500


def parse_csv(csv_text: str) -> dict:
    """Meme logique que consult.py::parse_csv : paires cle/valeur, 1ere ligne ignoree."""
    df = pd.read_csv(StringIO(csv_text), header=None, skiprows=1)
    codes = {}
    for _, row in df.iterrows():
        key = str(row[0]).strip()
        try:
            codes[key] = float(row[1])
        except (ValueError, TypeError):
            codes[key] = row[1]
    return codes


def compute_year_entry(codes: dict, year: int) -> dict:
    """Reprend les formules de consult.py::compute_kpis, mappees sur le schema hotel_gold."""

    def get(code):
        val = codes.get(code, 0.0)
        return val if isinstance(val, (int, float)) else 0.0

    ca                 = get("70")
    achats             = get("60")
    variation_stocks   = get("71")
    ebit               = get("9901")
    resultat_net       = get("9904")
    tresorerie         = get("54/58")
    dettes_financieres = get("17") + get("43")
    fonds_propres      = get("10/15")
    capital_souscrit   = get("100")

    def pct(num, denom):
        return round(num / denom * 100, 2) if denom else None

    def ratio(num, denom):
        return round(num / denom, 4) if denom else None

    return {
        "year":                year,
        "ca":                  ca,
        "achats":              achats,
        "variation_stocks":    variation_stocks,
        "ebit":                ebit,
        "resultat_net":        resultat_net,
        "tresorerie":          tresorerie,
        "dettes_financieres":  dettes_financieres,
        "fonds_propres":       fonds_propres,
        "capital_souscrit":    capital_souscrit,
        "schema_type":         codes.get("Model code"),
        "ratios": {
            "marge_brute":       ca - achats + variation_stocks,
            "marge_nette":       pct(resultat_net, ca),
            "roe":               pct(resultat_net, fonds_propres),
            "ratio_liquidite":   ratio(tresorerie, dettes_financieres),
            "taux_endettement":  pct(dettes_financieres, fonds_propres),
        },
    }


def build_gold() -> None:
    db   = get_db()
    hdfs = InsecureClient(HDFS_URL, user=HDFS_USER)

    log.info("=" * 60)
    log.info("BUILD GOLD — hotel_gold")
    log.info("=" * 60)

    # Index cree AVANT la boucle d'upsert (cf. bug de perf sur enterprise_silver au Jour 2).
    db.hotel_gold.create_index("enterprise_number", unique=True, name="idx_gold_enterprise_number")

    csv_files = list(db.download_state.find({
        "source":    "cbso",
        "file_type": "csv",
        "status":    "done",
    }))
    log.info(f"  {len(csv_files):,} CSV CBSO a traiter (download_state)")

    years_by_enterprise: dict[str, list[dict]] = {}
    errors = 0

    for i, doc in enumerate(csv_files, start=1):
        num       = doc["enterprise_number"]
        hdfs_path = doc["hdfs_path"]

        # mark_done() ne stocke pas "year" (ingest_cbso l'appelle sans ce champ) — on le
        # retrouve depuis le nom de fichier .../cbso/csvs/{annee}.csv, toujours fiable.
        year = int(doc["year"]) if doc.get("year") else None
        if year is None:
            m = re.search(r"/(\d{4})\.csv$", hdfs_path)
            year = int(m.group(1)) if m else None

        try:
            with hdfs.read(hdfs_path) as reader:
                csv_text = reader.read().decode("utf-8")
            codes = parse_csv(csv_text)
            year_entry = compute_year_entry(codes, year)
            years_by_enterprise.setdefault(num, []).append(year_entry)
        except Exception as exc:
            errors += 1
            log.error(f"  Erreur parsing {num}/{year} ({hdfs_path}) : {exc}")

        if i % 200 == 0:
            log.info(f"  {i:,}/{len(csv_files):,} CSV lus")

    log.info(f"  {len(years_by_enterprise):,} entreprises, {errors} erreur(s) de parsing")

    now = datetime.now(timezone.utc)
    ops = []
    for num, years in years_by_enterprise.items():
        years.sort(key=lambda y: y["year"])
        ops.append(UpdateOne(
            {"enterprise_number": num},
            {"$set": {"enterprise_number": num, "years": years, "last_updated": now}},
            upsert=True,
        ))
        if len(ops) >= BATCH_SIZE:
            db.hotel_gold.bulk_write(ops, ordered=False)
            ops = []

    if ops:
        db.hotel_gold.bulk_write(ops, ordered=False)

    total = db.hotel_gold.count_documents({})
    log.info("=" * 60)
    log.info(f"Termine — {total:,} entreprises dans hotel_gold")
    log.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.parse_args()
    build_gold()
