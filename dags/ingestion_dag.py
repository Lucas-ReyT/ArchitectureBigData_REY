"""
ingestion_dag.py
----------------
DAG Airflow — couche d'ingestion Bronze.

Flux :
  MongoDB → HDFS Bronze

Sources :
  - CBSO/NBB  : PDFs et CSVs des comptes annuels
  - eJustice  : publications (JSON)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from airflow.decorators import dag, task
from airflow.models.param import Param

log = logging.getLogger(__name__)

HDFS_URL    = "http://namenode:9870"
HDFS_USER   = "airflow"
HDFS_BRONZE = "/data/bronze"


@dag(
    dag_id="enterprise_ingestion",
    description="Ingestion Bronze — CBSO / eJustice → HDFS",
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=10,
    default_args={
        "retries":                   2,
        "retry_delay":               timedelta(minutes=5),
        "retry_exponential_backoff": True,
    },
    tags=["ingestion", "bronze", "layer-1"],
    params={
        "enterprise_number": Param(
            default="",
            type="string",
            description="Numéro BCE ciblé (vide = toutes les entreprises actives)",
        ),
        "start_year": Param(default=2020, type="integer"),
        "sources": Param(
            default=["cbso", "ejustice"],
            type="array",
            description="Sources à ingérer",
        ),
        "batch_size": Param(
            default=500,
            type="integer",
            description="Entreprises traitées par batch MongoDB",
        ),
        "mongo_skip": Param(
            default=0,
            type="integer",
            description="Offset MongoDB pour distribuer les runs parallèles",
        ),
        "sector": Param(
            default="",
            type="string",
            description="Secteur cible (ex. 'hotellerie') — si renseigné, la liste d'entreprises "
                        "vient de scrape_targets au lieu du mode bulk MongoDB",
        ),
    },
)
def enterprise_ingestion():

    # ── Task 1 : Résoudre la liste des entreprises 
    @task(task_id="resolve_enterprises")
    def resolve_enterprises(**context) -> list[str]:
        """
        Retourne la liste des numéros BCE à traiter.
        - Si enterprise_number fourni → [enterprise_number]
        - Sinon si sector fourni → cibles pending/in_progress depuis scrape_targets
        - Sinon → batch depuis MongoDB avec skip pour parallélisme
        """
        from db.mongo_client import get_db
        from db.state_db import get_pending_targets, mark_target_in_progress

        params = context["params"]
        num    = params.get("enterprise_number", "").strip()
        sector = params.get("sector", "").strip()

        if num:
            log.info(f"Mode ciblé : {num}")
            return [num]

        if sector:
            nums = get_pending_targets(sector, limit=params.get("batch_size"))
            for n in nums:
                mark_target_in_progress(n, sector)
            log.info(f"Mode sectoriel ({sector}) : {len(nums)} entreprises depuis scrape_targets")
            return nums

        db    = get_db()
        batch = params["batch_size"]
        skip  = params.get("mongo_skip", 0)
        nums  = [
            doc["enterprise_number"]
            for doc in db.enterprises.find(
                {"status": "AC"},
                {"enterprise_number": 1},
                skip=skip,
                limit=batch,
            )
        ]
        log.info(f"Mode bulk : {len(nums)} entreprises actives depuis MongoDB")
        return nums

    # ── Task 2 : Ingestion CBSO (PDFs + CSVs) 
    @task(task_id="ingest_cbso")
    def ingest_cbso(enterprise_numbers: list[str], **context) -> dict:
        """
        Pour chaque entreprise :
          1. Fetch liste des dépôts CBSO
          2. Delta detection via State DB
          3. Téléchargement du delta → HDFS Bronze
          4. Update State DB (done/error)
        """
        import time
        from hdfs import InsecureClient
        from db.state_db import is_done, mark_done, mark_error
        from scrapers.cbso_scraper import (
            fetch_deposit_list, filter_deposits,
            CBSO_DOC_BASE,
        )
        from scrapers.tor_session import get_with_rotation

        if "cbso" not in context["params"]["sources"]:
            log.info("[CBSO] Source désactivée — skip")
            return {}

        hdfs       = InsecureClient(HDFS_URL, user=HDFS_USER)
        start_year = context["params"]["start_year"]
        results    = {"done": 0, "error": 0, "skipped": 0}

        for num in enterprise_numbers:
            log.info(f"\n[CBSO] {num}")
            try:
                deposits  = fetch_deposit_list(num)
                par_annee = filter_deposits(deposits)
            except Exception as exc:
                log.error(f"[CBSO] {num} : fetch échoué — {exc}")
                continue

            for annee, depot in sorted(par_annee.items()):
                an  = int(annee) if annee.isdigit() else 0
                did = depot["id"]

                for file_type, min_year, url_fn in [
                    ("pdf", start_year, lambda d: f"{CBSO_DOC_BASE}/pdf/{d}"),
                    ("csv", start_year, lambda d: f"{CBSO_DOC_BASE}/consult/csv/{d}"),
                ]:
                    if an < min_year:
                        continue

                    if is_done(num, "cbso", did, file_type):
                        results["skipped"] += 1
                        continue

                    hdfs_path = f"{HDFS_BRONZE}/{num}/cbso/{file_type}s/{annee}.{file_type}"
                    time.sleep(1)

                    try:
                        resp = get_with_rotation(url_fn(did), timeout=60)

                        if resp.status_code == 200 and len(resp.content) > 500:
                            hdfs.makedirs(f"{HDFS_BRONZE}/{num}/cbso/{file_type}s")
                            with hdfs.write(hdfs_path, overwrite=True) as f:
                                f.write(resp.content)
                            mark_done(num, "cbso", did, file_type, hdfs_path, len(resp.content))
                            results["done"] += 1
                        else:
                            mark_error(num, "cbso", did, file_type,
                                       f"HTTP {resp.status_code} / taille {len(resp.content)}")
                            results["error"] += 1

                    except Exception as exc:
                        mark_error(num, "cbso", did, file_type, str(exc))
                        results["error"] += 1

            time.sleep(1)

        log.info(f"[CBSO] Résultat : {results}")
        return results

    # ── Task 3 : Ingestion eJustice 
    @task(task_id="ingest_ejustice")
    def ingest_ejustice(enterprise_numbers: list[str], **context) -> dict:
        """
        Scrape les publications eJustice et stocke en JSON dans HDFS Bronze.
        Une publication = 1 entrée dans State DB (deposit_id = numac).
        """
        import json, time
        from hdfs import InsecureClient
        from db.state_db import is_done, mark_done, mark_error
        from scrapers.ejustice_scraper import fetch_publications

        if "ejustice" not in context["params"]["sources"]:
            log.info("[eJustice] Source désactivée — skip")
            return {}

        hdfs    = InsecureClient(HDFS_URL, user=HDFS_USER)
        results = {"done": 0, "skipped": 0, "error": 0}

        for num in enterprise_numbers:
            log.info(f"\n[eJustice] {num}")
            deposit_id = f"ejustice_{num}"

            if is_done(num, "ejustice", deposit_id, "json"):
                log.info("  Déjà ingéré — skip")
                results["skipped"] += 1
                continue

            try:
                pubs      = fetch_publications(num, lang="fr")
                payload   = json.dumps(pubs, ensure_ascii=False, indent=2).encode()
                hdfs_path = f"{HDFS_BRONZE}/{num}/ejustice/publications.json"

                hdfs.makedirs(f"{HDFS_BRONZE}/{num}/ejustice")
                with hdfs.write(hdfs_path, overwrite=True) as f:
                    f.write(payload)

                mark_done(num, "ejustice", deposit_id, "json", hdfs_path, len(payload))
                results["done"] += 1

            except Exception as exc:
                mark_error(num, "ejustice", deposit_id, "json", str(exc))
                results["error"] += 1

            time.sleep(1)

        log.info(f"[eJustice] Résultat : {results}")
        return results

    # ── Task 4 : Rapport 
    @task(task_id="ingestion_report")
    def ingestion_report(
        enterprise_numbers: list[str],
        cbso_result: dict,
        ejustice_result: dict,
    ) -> dict:
        from db.state_db import get_stats

        log.info("\n" + "=" * 60)
        log.info("RAPPORT INGESTION BRONZE")
        log.info(f"  Entreprises traitées : {len(enterprise_numbers)}")
        log.info(f"  CBSO     : {cbso_result}")
        log.info(f"  eJustice : {ejustice_result}")

        if enterprise_numbers:
            stats = get_stats(enterprise_numbers[0])
            log.info(f"\n  State DB ({enterprise_numbers[0]}) : {stats}")

        log.info("=" * 60)

        return {
            "enterprises": len(enterprise_numbers),
            "cbso":        cbso_result,
            "ejustice":    ejustice_result,
        }

    # ── Task 5 : Mise à jour des cibles sectorielles (scrape_targets)
    @task(task_id="update_target_status")
    def update_target_status(enterprise_numbers: list[str], cbso_result: dict, **context) -> None:
        """
        Si `sector` est renseigné, marque chaque entreprise scrape_targets en status=done
        (avec filings_count = dépôts CBSO réussis) UNIQUEMENT si aucun dépôt n'est resté en
        erreur — sinon on laisse la cible en in_progress pour qu'elle soit retentée au
        prochain run (sinon une erreur transitoire la marquerait "done" à tort et elle ne
        serait plus jamais reprise).
        """
        from db.state_db import get_stats, mark_target_done

        sector = context["params"].get("sector", "").strip()
        if not sector:
            return

        for num in enterprise_numbers:
            stats = get_stats(num)
            done_count = sum(
                count for key, count in stats.items()
                if key.startswith("cbso/") and key.endswith("/done")
            )
            error_count = sum(
                count for key, count in stats.items()
                if key.startswith("cbso/") and key.endswith("/error")
            )
            if error_count == 0:
                mark_target_done(num, sector, done_count)
            else:
                log.info(f"[Targets] {num} : {error_count} erreur(s) restante(s) — cible laissée en in_progress")

        log.info(f"[Targets] {len(enterprise_numbers)} cibles mises à jour pour le secteur {sector}")

    # ── Câblage
    nums = resolve_enterprises()
    cbso = ingest_cbso(nums)
    ej   = ingest_ejustice(nums)
    ingestion_report(nums, cbso, ej)
    update_target_status(nums, cbso)


enterprise_ingestion()