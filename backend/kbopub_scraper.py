"""
kbopub_scraper.py
------------------
Scraping des dirigeants/representants (section "Functies") depuis kbopub.economie.fgov.be.
Copie autonome de dags/scrapers/kbopub_scraper.py (le backend tourne dans son propre
conteneur, sans acces au dossier dags/).
"""

import logging
import re

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

KBOPUB_BASE = "https://kbopub.economie.fgov.be/kbopub/toonondernemingps.html"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) "
        "Gecko/20100101 Firefox/128.0"
    ),
}

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
    """Scrape la section "Functies" (dirigeants/representants) pour une entreprise."""
    num_clean = enterprise_number.replace(".", "")

    try:
        resp = requests.get(
            KBOPUB_BASE, params={"ondernemingsnummer": num_clean}, headers=HEADERS, timeout=20
        )
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
        return []

    header_row = functies_h2.find_parent("tr")

    representatives = []
    for row in header_row.find_next_siblings("tr"):
        if row.find("h2") is not None:
            break

        cells = row.find_all("td")
        if len(cells) != 3:
            continue

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

    return representatives
