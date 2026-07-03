"""
kbopub_scraper.py
------------------
Scraping des dirigeants/representants (section "Functies") depuis kbopub.economie.fgov.be
pour une entreprise belge. Page HTML simple, pas de JS, pas besoin de navigateur.
"""

import logging
import re

from bs4 import BeautifulSoup

from scrapers.tor_session import make_session

logger = logging.getLogger(__name__)

KBOPUB_BASE = "https://kbopub.economie.fgov.be/kbopub/toonondernemingps.html"

# Traduction FR des roles les plus courants (page kbopub en neerlandais par defaut) —
# best-effort, le libelle brut est garde si absent de ce dict.
ROLE_LABELS_FR = {
    "Bestuurder":                 "Administrateur",
    "Gedelegeerd bestuurder":     "Administrateur delegue",
    "Zaakvoerder":                "Gerant",
    "Vast vertegenwoordiger":     "Representant permanent",
    "Vaste vertegenwoordiger":    "Representant permanent",
    "Voorzitter":                 "President",
    "Ondervoorzitter":            "Vice-president",
    "Commissaris":                "Commissaire",
    "Directeur":                  "Directeur",
    "Afgevaardigd bestuurder":    "Administrateur delegue",
    "Lid van het directiecomite": "Membre du comite de direction",
}

MONTHS_NL_TO_FR = {
    "januari": "janvier", "februari": "fevrier", "maart": "mars", "april": "avril",
    "mei": "mai", "juni": "juin", "juli": "juillet", "augustus": "aout",
    "september": "septembre", "oktober": "octobre", "november": "novembre", "december": "decembre",
}


def _translate_date(text: str) -> str:
    for nl, fr in MONTHS_NL_TO_FR.items():
        text = text.replace(nl, fr)
    return text


def fetch_representatives(enterprise_number: str) -> list[dict]:
    """
    Scrape la section "Functies" (dirigeants/representants) pour une entreprise.

    Parameters
    ----------
    enterprise_number : str   (format XXXX.XXX.XXX ou XXXXXXXXXX)

    Returns
    -------
    list[dict] : [{role, name, represented_enterprise_number, since}, ...]
    """
    num_clean = enterprise_number.replace(".", "")
    session = make_session()

    try:
        resp = session.get(KBOPUB_BASE, params={"ondernemingsnummer": num_clean}, timeout=20)
    except Exception as exc:
        logger.error(f"[kbopub] Erreur reseau pour {enterprise_number} : {exc}")
        return []

    if resp.status_code != 200:
        logger.error(f"[kbopub] Statut {resp.status_code} pour {enterprise_number}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    functies_h2 = next(
        (h2 for h2 in soup.find_all("h2") if h2.get_text(strip=True) == "Functies"),
        None,
    )
    if functies_h2 is None:
        logger.info(f"[kbopub] Pas de section Functies pour {enterprise_number}")
        return []

    header_row = functies_h2.find_parent("tr")

    representatives = []
    for row in header_row.find_next_siblings("tr"):
        if row.find("h2") is not None:
            break  # section suivante (Hoedanigheden, etc.)

        cells = row.find_all("td")
        if len(cells) != 3:
            continue  # ligne separatrice vide

        role_cell, name_cell, date_cell = cells

        role_raw = role_cell.get_text(strip=True)
        role = ROLE_LABELS_FR.get(role_raw, role_raw)

        link = name_cell.find("a")
        represented_number = link.get_text(strip=True).strip("()") if link else None

        name = name_cell.get_text(" ", strip=True)
        if link:
            name = name.replace(link.get_text(strip=True), "")
        name = re.sub(r"\s+", " ", name).strip()
        name = re.sub(r"\s*,\s*", ", ", name).strip(" ,()")

        since_text = date_cell.get_text(strip=True)
        since = _translate_date(since_text.replace("Sinds", "").strip()) or None

        representatives.append({
            "role": role,
            "name": name or None,
            "represented_enterprise_number": represented_number,
            "since": since,
        })

    logger.info(f"[kbopub] {len(representatives)} dirigeant(s) trouve(s) pour {enterprise_number}")
    return representatives
