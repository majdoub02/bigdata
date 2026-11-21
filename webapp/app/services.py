"""Clients Elasticsearch / MongoDB / Redis et vérifications de santé."""
import os
import time
from functools import lru_cache

import redis
from elasticsearch import Elasticsearch
from pymongo import MongoClient


@lru_cache(maxsize=1)
def es_client():
    return Elasticsearch(os.environ["ES_URL"], request_timeout=5, max_retries=0)


@lru_cache(maxsize=1)
def mongo_client():
    return MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=3000, tz_aware=True)


def mongo_db():
    return mongo_client()[os.getenv("MONGO_DB", "seclog")]


@lru_cache(maxsize=1)
def redis_client():
    return redis.Redis.from_url(os.environ["REDIS_URL"], socket_timeout=3, decode_responses=True)


def _timed(fn):
    """Exécute un test de connexion et mesure le temps de réponse."""
    start = time.perf_counter()
    try:
        detail, status = fn(), "up"
    except Exception as exc:  # noqa: BLE001 - on veut capturer toute erreur de connexion
        detail, status = f"{type(exc).__name__}: {exc}", "down"
    return {"status": status,
            "response_ms": round((time.perf_counter() - start) * 1000, 1),
            "detail": detail}


def _ping_es():
    return "cluster " + es_client().cluster.health()["status"]


def _ping_mongo():
    mongo_client().admin.command("ping")
    return "ping ok"


def _ping_redis():
    if not redis_client().ping():
        raise RuntimeError("pas de réponse PONG")
    return "pong"


def check_all():
    return {
        "elasticsearch": _timed(_ping_es),
        "mongodb": _timed(_ping_mongo),
        "redis": _timed(_ping_redis),
    }


def overall_status(checks):
    ups = [c["status"] == "up" for c in checks.values()]
    if all(ups):
        return "healthy"
    return "down" if not any(ups) else "degraded"
