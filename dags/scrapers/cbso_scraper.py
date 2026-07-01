"""
cbso_scraper.py
---------------
Récupération des comptes annuels depuis consult.cbso.nbb.be, stockés dans HDFS.
"""

import logging
import time

from scrapers.tor_session import get_with_rotation

logger = logging.getLogger(__name__)

CBSO_API      = "https://consult.cbso.nbb.be/api/rs-consult/published-deposits"
CBSO_DOC_BASE = "https://consult.cbso.nbb.be/api/external/broker/public/deposits"
CBSO_HOME     = "https://consult.cbso.nbb.be/consult-enterprise"


def fetch_deposit_list(enterprise_number: str) -> list[dict]:
    """
    Récupère tous les dépôts CBSO pour une entreprise.

    Parameters
    ----------
    enterprise_number : str
        Numéro BCE au format XXXX.XXX.XXX ou XXXXXXXXXX

    Returns
    -------
    list[dict] : liste brute des dépôts retournés par l'API CBSO
    """
    num_clean = enterprise_number.replace(".", "")
    init_url  = f"{CBSO_HOME}/{num_clean}"

    deposits = []
    page     = 0

    while True:
        params = {
            "enterpriseNumber": num_clean,
            "page": page,
            "size": 50,
            "sort": ["periodEndDate,desc", "depositDate,desc"],
        }

        logger.info(f"[CBSO] Fetch dépôts page={page} pour {enterprise_number}")

        resp = get_with_rotation(
            url=CBSO_API,
            params=params,
            extra_headers={"Referer": init_url},
            init_session_url=init_url,
        )

        if resp.status_code != 200:
            logger.error(f"[CBSO] API retourne {resp.status_code}")
            break

        if not resp.content:
            break

        data = resp.json()
        batch = data.get("content", [])
        deposits.extend(batch)

        logger.info(f"[CBSO] Page {page} → {len(batch)} dépôts | last={data.get('last', True)}")

        if not batch or data.get("last", True):
            break

        page += 1
        time.sleep(1.5)  # politesse entre pages

    logger.info(f"[CBSO] Total : {len(deposits)} dépôts pour {enterprise_number}")
    return deposits


def filter_deposits(deposits: list[dict]) -> dict[str, dict]:
    """
    Filtre les dépôts consolidés et conserve le meilleur dépôt par année.

    Priorité : langue FR > langue NL > premier disponible
    """
    # Exclure les comptes consolidés
    deposits = [
        d for d in deposits
        if "consolidé" not in d.get("modelName", "").lower()
        and "geconsolideerde" not in d.get("modelName", "").lower()
    ]

    par_annee: dict[str, dict] = {}

    for d in deposits:
        annee = str(d.get("periodEndDateYear", "?"))
        if annee == "?":
            continue

        if annee not in par_annee:
            par_annee[annee] = d
        else:
            # Préférer FR
            existing_lang = par_annee[annee].get("language", "")
            new_lang      = d.get("language", "")
            if new_lang == "FR" and existing_lang != "FR":
                par_annee[annee] = d

    logger.info(f"[CBSO] Filtrage : {len(par_annee)} années uniques")
    return par_annee


