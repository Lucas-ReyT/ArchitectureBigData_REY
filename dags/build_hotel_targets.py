"""
build_hotel_targets.py
-----------------------
Filtre enterprises (Bronze) pour extraire les entreprises du secteur hotelier et les
charge dans scrape_targets (status=pending) pour que le DAG enterprise_ingestion
puisse les scraper via le parametre `sector`.
"""

import argparse
import logging

from db.mongo_client import get_db, init_indexes
from db.state_db import create_targets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

SECTOR = "hotellerie"

HOTEL_NACE_CODES = [
    "55100",  # Hotels et hebergement similaire
    "55201",  # Auberges de jeunesse
    "55202",  # Centres et villages de vacances
    "55203",  # Gites de vacances, appartements et meubles de vacances
    "55204",  # Chambres d'hotes
    "55209",  # Autres hebergements de courte duree n.c.a.
    "55300",  # Terrains de camping et parcs pour caravanes
    "55400",  # Intermediation pour l'hebergement (Nace2025, type Airbnb/Booking)
    "55900",  # Autres hebergements
]

EXCLUDED_LEGAL_FORMS = [
    "110", "114", "116", "117",             # entites publiques
    "301", "302", "303",                    # services federaux
    "310", "320", "330", "340", "350",       # autorites regionales
    "400",                                   # communes, CPAS, intercommunales
    "411", "412", "413", "414", "415",
    "416", "417", "418", "419", "420",
]


def find_hotel_enterprises(sector_codes: list[str] = HOTEL_NACE_CODES) -> list[str]:
    db = get_db()
    query = {
        "status": "AC",
        "TypeOfEnterprise": "2",
        "legal_form": {"$nin": EXCLUDED_LEGAL_FORMS},
        "activities": {
            "$elemMatch": {
                "classification": "MAIN",
                "nace_code": {"$in": sector_codes},
            }
        },
    }
    return [doc["enterprise_number"] for doc in db.enterprises.find(query, {"enterprise_number": 1})]


def build_hotel_targets() -> None:
    log.info("=" * 60)
    log.info(f"CIBLAGE SECTORIEL — {SECTOR}")
    log.info("=" * 60)

    init_indexes()

    enterprise_numbers = find_hotel_enterprises()
    log.info(f"  {len(enterprise_numbers):,} entreprises hotelieres actives trouvees dans enterprises")

    created = create_targets(SECTOR, enterprise_numbers)

    log.info("=" * 60)
    log.info(f"Termine — {created:,} nouvelles cibles (sur {len(enterprise_numbers):,}) en status=pending")
    log.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.parse_args()
    build_hotel_targets()
