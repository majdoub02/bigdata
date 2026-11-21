"""Accès MongoDB : métadonnées des fichiers et historique des recherches."""
import os
from datetime import datetime, timezone

from pymongo import ReturnDocument

from . import services

FILE_STATUSES = {"pending", "indexing", "indexed", "partial", "error"}


def now():
    return datetime.now(timezone.utc)


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def serialize(doc):
    """Document MongoDB -> dict JSON (dates ISO, _id renommé en file_id)."""
    if doc is None:
        return None
    out = dict(doc)
    if "_id" in out:
        out["file_id"] = out.pop("_id")
    for key, value in out.items():
        if isinstance(value, datetime):
            out[key] = _aware(value).isoformat()
    return out


def new_file_id():
    """Identifiant unique f_AAAAMMJJ_NNN (compteur atomique par jour)."""
    day = now().strftime("%Y%m%d")
    doc = services.mongo_db()["counters"].find_one_and_update(
        {"_id": f"files:{day}"}, {"$inc": {"seq": 1}}, upsert=True,
        return_document=ReturnDocument.AFTER)
    return f"f_{day}_{doc['seq']:03d}"


def create_file_record(file_id, filename, size, validated, user="anonymous"):
    doc = {"_id": file_id, "filename": filename, "size": size, "type": validated.fmt,
           "rows": validated.rows, "uploaded_at": now(), "status": "pending",
           "nb_logs": 0, "user": user, "error": None}
    services.mongo_db()["files"].insert_one(doc)
    return serialize(doc)


def get_file(file_id):
    return services.mongo_db()["files"].find_one({"_id": file_id})


def update_status(file_id, status, nb_logs=None, error=None):
    fields = {"status": status, "updated_at": now()}
    if nb_logs is not None:
        fields["nb_logs"] = nb_logs
    if error is not None:
        fields["error"] = error
    return services.mongo_db()["files"].find_one_and_update(
        {"_id": file_id}, {"$set": fields}, return_document=ReturnDocument.AFTER)


def list_files(status=None, page=1, size=20):
    col = services.mongo_db()["files"]
    query = {"status": status} if status else {}
    total = col.count_documents(query)
    cursor = col.find(query).sort("uploaded_at", -1).skip((page - 1) * size).limit(size)
    return total, [serialize(d) for d in cursor]


def count_files():
    return services.mongo_db()["files"].count_documents({})


def compute_status(rec, es_count, at=None):
    """Statut d'ingestion d'après le nombre de logs trouvés dans Elasticsearch."""
    at = at or now()
    age = (at - _aware(rec["uploaded_at"])).total_seconds()
    expected = rec.get("rows") or 0
    if expected and es_count >= expected:
        return "indexed"
    if es_count > 0:
        return "indexing" if age < 120 else "partial"
    return "pending" if age < 60 else "error"


def save_to_inbox(file_id, fmt, content):
    """Écrit le fichier dans le dossier surveillé par Logstash (écriture atomique)."""
    inbox = os.getenv("INBOX_DIR", "/data/inbox")
    os.makedirs(inbox, exist_ok=True)
    path = os.path.join(inbox, f"{file_id}.{fmt}")
    tmp = path + ".tmp"   # Logstash ne lit pas les *.tmp : jamais de fichier incomplet
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    os.replace(tmp, path)
    return path


def save_search(user, query, filters, results_count):
    services.mongo_db()["search_history"].insert_one(
        {"user": user, "query": query, "filters": filters,
         "results_count": results_count, "created_at": now()})


def list_searches(limit=20):
    cursor = services.mongo_db()["search_history"].find().sort("created_at", -1).limit(limit)
    out = []
    for d in cursor:
        d = serialize(d)
        d["id"] = str(d.pop("file_id", ""))
        out.append(d)
    return out
