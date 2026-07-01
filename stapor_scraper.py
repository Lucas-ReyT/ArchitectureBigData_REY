
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import requests
from hdfs import InsecureClient
from playwright.sync_api import sync_playwright

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
BASE        = "https://statuts.notaire.be/stapor_v1"
COOKIE_FILE = Path("notaire_cookies.json")
PAGE_SIZE   = 20
HDFS_URL    = os.getenv("HDFS_URL", "http://localhost:9870")
HDFS_USER   = os.getenv("HDFS_USER", "root")

HEADERS_API = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "fr-BE,fr;q=0.9,en;q=0.8",
    "sec-fetch-dest":  "empty",
    "sec-fetch-mode":  "cors",
    "sec-fetch-site":  "same-origin",
}


# ── Session Playwright ────────────────────────────────────────────────────────

def _running_in_async_loop() -> bool:
    """Jupyter/IPython tourne dans une boucle asyncio — Playwright sync doit être isolé."""
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _fetch_cookies_via_playwright_impl(seed_bce: str) -> list:
    seed_url = (
        f"{BASE}/enterprise/{seed_bce}/statutes"
        f"?enterpriseNumber={seed_bce}&statuteStart=0&statuteCount=5"
    )
    log.info("Ouverture Chrome pour renouveler les cookies F5...")

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=False)
        except Exception:
            browser = p.chromium.launch(headless=False)

        ctx  = browser.new_context(locale="fr-BE", user_agent=HEADERS_API["User-Agent"])
        page = ctx.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page.goto("https://statuts.notaire.be/", wait_until="load", timeout=20_000)
        page.wait_for_timeout(2000)
        page.goto(seed_url, wait_until="load", timeout=30_000)

        for i in range(40):
            names = {c["name"] for c in ctx.cookies()}
            if "OClmoOot" in names and "Lyp1CWKh" in names:
                log.info(f"  Cookies OK ({i * 500}ms)")
                break
            page.wait_for_timeout(500)
        else:
            log.warning(f"  Timeout — cookies présents : {[c['name'] for c in ctx.cookies()]}")

        cookies = ctx.cookies()
        browser.close()
    return cookies


def _fetch_cookies_via_playwright(seed_bce: str) -> list:
    """Lance Playwright dans un thread si appelé depuis Jupyter."""
    if _running_in_async_loop():
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_fetch_cookies_via_playwright_impl, seed_bce).result()
    return _fetch_cookies_via_playwright_impl(seed_bce)


def _build_session(cookies: list) -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS_API)
    for c in cookies:
        session.cookies.set(c["name"], c["value"], domain=c["domain"])
    return session


def _session_valid(session: requests.Session, bce: str) -> bool:
    num = bce.replace(".", "")
    session.headers["Referer"] = (
        f"{BASE}/enterprise/{num}/statutes"
        f"?enterpriseNumber={num}&statuteStart=0&statuteCount=5"
    )
    try:
        r = session.get(
            f"{BASE}/api/enterprises/{num}/statutes",
            params={"offset": 0, "limit": 1},
            timeout=10,
        )
        return "application/json" in r.headers.get("content-type", "")
    except Exception:
        return False


def get_session(seed_bce: str = "0836157420", force_refresh: bool = False) -> requests.Session:
    """Retourne une session valide — rouvre Chrome si cookies expirés ou entreprise différente."""
    bce = seed_bce.replace(".", "")
    if not force_refresh and COOKIE_FILE.exists():
        session = _build_session(json.loads(COOKIE_FILE.read_text()))
        if _session_valid(session, bce):
            log.info(f"Session OK (cache) pour {bce}")
            return session
        log.info(f"Cookies invalides pour {bce} — renouvellement...")

    cookies = _fetch_cookies_via_playwright(bce)
    COOKIE_FILE.write_text(json.dumps(cookies, indent=2))
    session = _build_session(cookies)
    if not _session_valid(session, bce):
        log.warning(f"Session encore invalide après Playwright pour {bce}")
    return session


# ── Fetch statuts ─────────────────────────────────────────────────────────────

def get_statutes(
    session: requests.Session,
    enterprise_number: str,
    *,
    _retry: bool = True,
) -> tuple[requests.Session, list]:
    """Récupère tous les statuts DONE pour une entreprise. Retourne (session, statuts)."""
    num = enterprise_number.replace(".", "")
    url = f"{BASE}/api/enterprises/{num}/statutes"
    session.headers["Referer"] = (
        f"{BASE}/enterprise/{num}/statutes"
        f"?enterpriseNumber={num}&statuteStart=0&statuteCount=5"
    )

    all_statutes, offset = [], 0
    while True:
        r = session.get(
            url,
            params={"deedDate": "", "offset": offset, "limit": PAGE_SIZE},
            timeout=15,
        )
        r.raise_for_status()

        if "application/json" not in r.headers.get("content-type", ""):
            if _retry:
                log.warning(f"[{num}] Session expirée — renouvellement cookies...")
                session = get_session(seed_bce=num)
                return get_statutes(session, enterprise_number, _retry=False)
            log.error(f"[{num}] Session expirée (abandon)")
            break

        data  = r.json()
        batch = data.get("statutes", [])
        all_statutes.extend(batch)
        log.info(f"  [{num}] offset={offset} — {len(batch)} statuts (total: {data.get('totalItems', 0)})")

        if not batch or len(all_statutes) >= data.get("totalItems", 0):
            break
        offset += PAGE_SIZE
        time.sleep(0.3)

    done = [s for s in all_statutes if s.get("documentStatus") == "DONE"]
    log.info(f"  [{num}] → {len(done)} statuts DONE")
    return session, done


# ── Téléchargement PDF → HDFS ─────────────────────────────────────────────────

def download_statute_pdf(
    session: requests.Session,
    enterprise_number: str,
    statute: dict,
    hdfs: InsecureClient,
) -> Optional[str]:
    """Télécharge un PDF et le stocke dans HDFS. Retourne le chemin HDFS ou None."""
    num       = enterprise_number.replace(".", "")
    doc_id    = statute["documentId"]
    deed_date = statute.get("deedDate", "unknown").replace("-", "")
    hdfs_path = f"/data/nbb/stapor/{num}/{deed_date}_{doc_id}.pdf"

    # Skip si déjà présent
    try:
        hdfs.status(hdfs_path)
        log.info(f"    Déjà dans HDFS : {hdfs_path}")
        return hdfs_path
    except Exception:
        pass

    r = session.get(
        f"{BASE}/api/enterprises/{num}/statutes/non-certified/{doc_id}",
        timeout=30,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    if "pdf" not in r.headers.get("content-type", "") and len(r.content) < 1000:
        return None

    with hdfs.write(hdfs_path, overwrite=True) as f:
        f.write(r.content)
    log.info(f"    → HDFS : {hdfs_path} ({len(r.content) // 1024} KB)")
    return hdfs_path


# ── Pipeline complet ──────────────────────────────────────────────────────────

def run(enterprise_numbers: list, seed_bce: Optional[str] = None) -> dict:
    """
    Point d'entrée principal.

    Parameters
    ----------
    enterprise_numbers : liste de numéros BCE (format XXXX.XXX.XXX ou XXXXXXXXXX)
    seed_bce           : numéro utilisé pour le warm-up Playwright
                         (défaut = premier de la liste)

    Returns
    -------
    dict {enterprise_number: [statute_dict, ...]}
    Chaque statute_dict contient les métadonnées + "hdfs_path"
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    seed = (seed_bce or enterprise_numbers[0]).replace(".", "")
    session = get_session(seed_bce=seed)
    hdfs    = InsecureClient(HDFS_URL, user=HDFS_USER)

    results = {}
    for i, num in enumerate(enterprise_numbers):
        log.info(f"\n{'='*55}\n  Entreprise : {num}\n{'='*55}")

        bce = num.replace(".", "")
        if not _session_valid(session, bce):
            log.info(f"Nouvelle session requise pour {num}")
            session = get_session(seed_bce=bce)

        session, statutes = get_statutes(session, num)
        downloaded = []

        for statute in statutes:
            hdfs_path = download_statute_pdf(session, num, statute, hdfs)
            downloaded.append({**statute, "hdfs_path": hdfs_path})
            time.sleep(0.3)

        results[num] = downloaded

        ok  = sum(1 for s in downloaded if s["hdfs_path"])
        nok = len(downloaded) - ok
        log.info(f"  Résultat : {ok} PDFs stockés, {nok} ignorés")

        if i + 1 < len(enterprise_numbers):
            time.sleep(2)

    return results