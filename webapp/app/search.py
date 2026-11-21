"""Recherche dans Elasticsearch : paramètres, Query DSL, cache Redis, historique."""
import hashlib
import ipaddress
import json
import logging
from datetime import date

from . import repository, services
from .errors import ApiError
from .validation import parse_timestamp

log = logging.getLogger("webapp.search")

INDEX = "logs-security-*"
LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
SORT_FIELDS = {"@timestamp", "level", "service"}
DEFAULT_SIZE, MAX_SIZE, MAX_WINDOW = 50, 100, 10000
CACHE_TTL = 60
TEXT_FIELDS = ["message", "username", "resource", "source_ip", "attack_type", "service", "event_type"]


def bad(message):
    return ApiError(400, "invalid_parameter", message)


def _int(args, name, default, minimum=1, maximum=None):
    raw = args.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        raise bad(f"Le paramètre '{name}' doit être un entier.") from None
    if value < minimum or (maximum and value > maximum):
        limit = f" entre {minimum} et {maximum}" if maximum else f" supérieur ou égal à {minimum}"
        raise bad(f"Le paramètre '{name}' doit être{limit}.")
    return value


def _date(raw, name, end=False):
    """Accepte AAAA-MM-JJ ou un horodatage ISO 8601 ; renvoie une valeur utilisable par Elasticsearch."""
    raw = raw.strip()
    try:
        date.fromisoformat(raw)
        return raw + "||/d" if end else raw     # fin de journée incluse pour 'to'
    except ValueError:
        pass
    try:
        parse_timestamp(raw)
    except ValueError:
        raise bad(f"Date invalide pour '{name}' : {raw!r} (format AAAA-MM-JJ ou ISO 8601).") from None
    return raw


def parse_params(args):
    """Valide les paramètres de la requête et renvoie un dict normalisé."""
    p = {"q": (args.get("q") or "").strip() or None}

    levels = [x.strip().upper() for x in (args.get("level") or "").split(",") if x.strip()]
    unknown = [x for x in levels if x not in LEVELS]
    if unknown:
        raise bad(f"Niveau(x) inconnu(s) : {', '.join(unknown)}. Valeurs : {', '.join(sorted(LEVELS))}.")
    p["level"] = levels or None

    service = (args.get("service") or "").strip().lower()
    p["service"] = service or None

    ip = (args.get("source_ip") or "").strip()
    if ip:
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            raise bad(f"Adresse IP invalide : {ip!r}.") from None
    p["source_ip"] = ip or None

    p["from"] = _date(args["from"], "from") if args.get("from") else None
    p["to"] = _date(args["to"], "to", end=True) if args.get("to") else None

    sort = (args.get("sort") or "@timestamp:desc").split(":")
    field, order = sort[0], (sort[1] if len(sort) > 1 else "desc")
    if field not in SORT_FIELDS or order not in ("asc", "desc"):
        raise bad("Paramètre 'sort' invalide. Exemple : @timestamp:desc (champs : "
                  + ", ".join(sorted(SORT_FIELDS)) + ").")
    p["sort"] = f"{field}:{order}"

    p["page"] = _int(args, "page", 1)
    p["size"] = _int(args, "size", DEFAULT_SIZE, maximum=MAX_SIZE)
    if p["page"] * p["size"] > MAX_WINDOW:
        raise bad(f"Fenêtre de résultats limitée à {MAX_WINDOW} documents : affinez votre recherche.")
    return p


def build_query(p):
    """Construit les arguments de es.search() (Query DSL) à partir des paramètres validés."""
    must, flt = [], []
    if p["q"]:
        must.append({"simple_query_string": {"query": p["q"], "fields": TEXT_FIELDS,
                                             "default_operator": "and", "lenient": True}})
    if p["level"]:
        flt.append({"terms": {"level": p["level"]}})
    if p["service"]:
        flt.append({"term": {"service": p["service"]}})
    if p["source_ip"]:
        flt.append({"term": {"source_ip": p["source_ip"]}})
    if p["from"] or p["to"]:
        rng = {}
        if p["from"]:
            rng["gte"] = p["from"]
        if p["to"]:
            rng["lte"] = p["to"]
        flt.append({"range": {"@timestamp": rng}})

    field, order = p["sort"].split(":")
    sort = [{field: {"order": order, "unmapped_type": "keyword"}}]
    if field != "@timestamp":
        sort.append({"@timestamp": {"order": "desc"}})

    bool_query = {"filter": flt}
    bool_query["must"] = must or [{"match_all": {}}]
    return {"query": {"bool": bool_query}, "sort": sort,
            "from_": (p["page"] - 1) * p["size"], "size": p["size"], "track_total_hits": True}


def format_response(resp, p):
    total = resp["hits"]["total"]["value"]
    hits = []
    for h in resp["hits"]["hits"]:
        row = dict(h["_source"])
        row["id"], row["index"] = h["_id"], h["_index"]
        hits.append(row)
    return {"total": total, "page": p["page"], "size": p["size"],
            "pages": (total + p["size"] - 1) // p["size"], "took_ms": resp.get("took"), "hits": hits}


def cache_key(p):
    digest = hashlib.sha1(json.dumps(p, sort_keys=True).encode()).hexdigest()
    return f"cache:search:{digest}"


def _cache_get(key):
    try:
        raw = services.redis_client().get(key)
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 - le cache ne doit jamais casser la recherche
        log.warning("Redis indisponible (lecture du cache)")
        return None


def _cache_set(key, value, ttl):
    try:
        services.redis_client().set(key, json.dumps(value), ex=ttl)
    except Exception:  # noqa: BLE001
        log.warning("Redis indisponible (écriture du cache)")


def run_search(args, user="anonymous"):
    p = parse_params(args)
    key = cache_key(p)
    result = _cache_get(key)
    cached = result is not None
    if not cached:
        try:
            resp = services.es_client().search(index=INDEX, **build_query(p))
        except Exception:  # noqa: BLE001
            log.exception("Erreur Elasticsearch pendant la recherche")
            raise ApiError(503, "elasticsearch_unavailable", "Elasticsearch ne répond pas.") from None
        result = format_response(resp, p)
        _cache_set(key, result, CACHE_TTL)
    # Historique des recherches (MongoDB) : ne doit pas faire échouer la recherche
    try:
        filters = {k: v for k, v in p.items() if k not in ("q", "page", "size") and v is not None}
        repository.save_search(user, p["q"], filters, result["total"])
    except Exception:  # noqa: BLE001
        log.warning("MongoDB indisponible (historique non enregistré)")
    return {**result, "cached": cached}


def get_log(log_id):
    try:
        resp = services.es_client().search(index=INDEX, query={"ids": {"values": [log_id]}}, size=1)
    except Exception:  # noqa: BLE001
        log.exception("Erreur Elasticsearch (détail d'un log)")
        raise ApiError(503, "elasticsearch_unavailable", "Elasticsearch ne répond pas.") from None
    hits = resp["hits"]["hits"]
    if not hits:
        raise ApiError(404, "log_not_found", f"Aucun log avec l'identifiant {log_id!r}.")
    row = dict(hits[0]["_source"])
    row["id"], row["index"] = hits[0]["_id"], hits[0]["_index"]
    return row
