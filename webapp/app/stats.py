"""Statistiques globales pour le dashboard (une seule requête d'agrégation)."""
import json
import logging

from . import repository, search, services
from .errors import ApiError

log = logging.getLogger("webapp.stats")
CACHE_KEY, CACHE_TTL = "cache:stats", 30


def build_aggs():
    return {
        "today": {"filter": {"range": {"@timestamp": {"gte": "now/d"}}}},
        "errors": {"filter": {"terms": {"level": ["ERROR", "CRITICAL"]}}},
        "by_level": {"terms": {"field": "level", "size": 10}},
        "top_failed_ips": {"filter": {"term": {"status": "failure"}},
                           "aggs": {"ips": {"terms": {"field": "source_ip", "size": 5}}}},
        "timeline": {"filter": {"range": {"@timestamp": {"gte": "now-7d/d"}}},
                     "aggs": {"per_bucket": {"date_histogram": {"field": "@timestamp",
                                                                "fixed_interval": "3h"}}}},
    }


def format_stats(resp, files_count):
    aggs = resp["aggregations"]
    return {
        "total_logs": resp["hits"]["total"]["value"],
        "logs_today": aggs["today"]["doc_count"],
        "error_logs": aggs["errors"]["doc_count"],
        "files_uploaded": files_count,
        "by_level": {b["key"]: b["doc_count"] for b in aggs["by_level"]["buckets"]},
        "top_failed_ips": [{"ip": b["key"], "count": b["doc_count"]}
                           for b in aggs["top_failed_ips"]["ips"]["buckets"]],
        "timeline": [{"time": b.get("key_as_string", b["key"]), "count": b["doc_count"]}
                     for b in aggs["timeline"]["per_bucket"]["buckets"]],
    }


def get_stats():
    try:
        raw = services.redis_client().get(CACHE_KEY)
        if raw:
            return {**json.loads(raw), "cached": True}
    except Exception:  # noqa: BLE001
        log.warning("Redis indisponible (cache des stats)")
    try:
        resp = services.es_client().search(index=search.INDEX, size=0, track_total_hits=True,
                                           aggs=build_aggs())
    except Exception:  # noqa: BLE001
        log.exception("Erreur Elasticsearch (statistiques)")
        raise ApiError(503, "elasticsearch_unavailable", "Elasticsearch ne répond pas.") from None
    data = format_stats(resp, repository.count_files())
    try:
        services.redis_client().set(CACHE_KEY, json.dumps(data), ex=CACHE_TTL)
    except Exception:  # noqa: BLE001
        log.warning("Redis indisponible (écriture du cache des stats)")
    return {**data, "cached": False}
