"""
main.py
-------
API FastAPI servant les donnees Silver (enterprise_silver) et Gold (hotel_gold) au frontend.
"""

import os
import re
from datetime import datetime, timezone
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from hdfs import InsecureClient
from pymongo import MongoClient
from pymongo.database import Database

from kbopub_scraper import fetch_representatives

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27018")
MONGO_DB  = os.getenv("MONGO_DB",  "belgique")
HDFS_URL  = os.getenv("HDFS_URL",  "http://namenode:9870")
HDFS_USER = os.getenv("HDFS_USER", "airflow")

BCE_NUMBER_RE = re.compile(r"^[\d.]+$")

MEDIA_TYPES = {"pdf": "application/pdf", "csv": "text/csv"}


@lru_cache(maxsize=1)
def get_client() -> MongoClient:
    return MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)


def get_db() -> Database:
    return get_client()[MONGO_DB]


@lru_cache(maxsize=1)
def get_hdfs() -> InsecureClient:
    return InsecureClient(HDFS_URL, user=HDFS_USER)


app = FastAPI(title="Hotel Gold API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _strip_id(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


@app.get("/api/enterprises")
def search_enterprises(q: str = Query(..., min_length=1), limit: int = 20, scraped_only: bool = False):
    db = get_db()
    q = q.strip()

    if BCE_NUMBER_RE.match(q):
        query = {"enterprise_number": {"$regex": "^" + re.escape(q)}}
    else:
        query = {"name": {"$regex": re.escape(q), "$options": "i"}}

    # Bouton demo : ne montrer que les entreprises deja scrapees (presentes dans hotel_gold).
    if scraped_only:
        query = {"$and": [query, {"enterprise_number": {"$in": db.hotel_gold.distinct("enterprise_number")}}]}

    results = db.enterprise_silver.find(
        query,
        {
            "enterprise_number": 1,
            "name": 1,
            "status_label": 1,
            "legal_form_label": 1,
            "address.city_fr": 1,
        },
    ).limit(limit)

    return [_strip_id(doc) for doc in results]


@app.get("/api/enterprises/{enterprise_number}")
def get_enterprise(enterprise_number: str):
    db = get_db()

    silver = db.enterprise_silver.find_one({"enterprise_number": enterprise_number})
    if silver is None:
        raise HTTPException(status_code=404, detail="Entreprise introuvable")

    gold = db.hotel_gold.find_one({"enterprise_number": enterprise_number})

    return {
        "silver": _strip_id(silver),
        "gold": _strip_id(gold) if gold else None,
    }


@app.get("/api/enterprises/{enterprise_number}/representatives")
def get_representatives(enterprise_number: str):
    """
    Dirigeants/representants (kbopub). Scrape une seule fois, puis sert depuis Mongo —
    ne relance jamais le scraper si deja en base (meme comportement attendu que les
    statuts notaire : scrape a la demande, persiste, ensuite lecture seule).
    """
    db = get_db()

    cached = db.representatives.find_one({"enterprise_number": enterprise_number})
    if cached is not None:
        return _strip_id(cached)

    reps = fetch_representatives(enterprise_number)
    doc = {
        "enterprise_number": enterprise_number,
        "representatives": reps,
        "scraped_at": datetime.now(timezone.utc),
    }
    db.representatives.update_one(
        {"enterprise_number": enterprise_number}, {"$set": doc}, upsert=True
    )
    return _strip_id(doc)


@app.get("/api/enterprises/{enterprise_number}/documents")
def list_documents(enterprise_number: str):
    """Liste les PDF/CSV CBSO deja telecharges avec succes pour cette entreprise."""
    db = get_db()
    docs = db.download_state.find(
        {"enterprise_number": enterprise_number, "source": "cbso", "status": "done"},
        {"file_type": 1, "hdfs_path": 1, "size_bytes": 1, "_id": 0},
    ).sort("hdfs_path", 1)

    results = []
    for doc in docs:
        m = re.search(r"/(\d{4})\." + doc["file_type"] + r"$", doc["hdfs_path"])
        results.append({
            "year": int(m.group(1)) if m else None,
            "file_type": doc["file_type"],
            "size_bytes": doc.get("size_bytes"),
        })
    return sorted(results, key=lambda d: (d["year"] or 0, d["file_type"]))


@app.get("/api/documents/{enterprise_number}/{year}/{file_type}")
def download_document(enterprise_number: str, year: int, file_type: str):
    if file_type not in MEDIA_TYPES:
        raise HTTPException(status_code=400, detail="Type de fichier invalide")

    db = get_db()
    doc = db.download_state.find_one({
        "enterprise_number": enterprise_number,
        "source": "cbso",
        "file_type": file_type,
        "status": "done",
        "hdfs_path": {"$regex": f"/{year}\\.{file_type}$"},
    })
    if doc is None:
        raise HTTPException(status_code=404, detail="Document introuvable")

    hdfs = get_hdfs()
    with hdfs.read(doc["hdfs_path"]) as reader:
        content = reader.read()

    filename = f"{enterprise_number}_{year}.{file_type}"
    return StreamingResponse(
        iter([content]),
        media_type=MEDIA_TYPES[file_type],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
