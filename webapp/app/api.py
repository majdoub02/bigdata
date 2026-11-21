"""Routes de l'API : upload, recherche, logs, fichiers, statistiques."""
import logging

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

from . import repository, search, services, stats
from .errors import ApiError
from .validation import validate_and_normalize

log = logging.getLogger("webapp.api")
bp = Blueprint("api", __name__)


def _read_upload():
    file = request.files.get("file")
    if file is None or not file.filename:
        raise ApiError(400, "missing_file", "Aucun fichier reçu (champ multipart 'file').")
    filename = secure_filename(file.filename) or "upload"
    return filename, file.read()


# ---------- Upload ----------
@bp.post("/upload")
def upload():
    filename, raw = _read_upload()
    validated = validate_and_normalize(filename, raw)
    file_id = repository.new_file_id()
    record = repository.create_file_record(file_id, filename, len(raw), validated)
    try:
        repository.save_to_inbox(file_id, validated.fmt, validated.content)
    except OSError as exc:
        log.exception("Impossible d'écrire dans le dossier surveillé")
        repository.update_status(file_id, "error", error=str(exc))
        raise ApiError(500, "storage_error", "Impossible d'enregistrer le fichier sur le serveur.") from None
    log.info("Fichier %s accepté (%s, %d lignes)", file_id, validated.fmt, validated.rows)
    return jsonify({"file_id": file_id, "status": "pending", "format": validated.fmt,
                    "rows": validated.rows, "warnings": validated.warnings,
                    "message": "Fichier accepté : l'ingestion par Logstash démarre.",
                    "file": record}), 202


@bp.post("/upload/preview")
def upload_preview():
    """Valide le fichier et renvoie les 10 premières lignes, sans rien enregistrer."""
    filename, raw = _read_upload()
    validated = validate_and_normalize(filename, raw)
    return jsonify({"valid": True, "format": validated.fmt, "rows": validated.rows,
                    "preview": validated.preview, "warnings": validated.warnings})


# ---------- Fichiers ----------
@bp.get("/api/v1/files")
def list_files():
    status = request.args.get("status")
    if status and status not in repository.FILE_STATUSES:
        raise ApiError(400, "invalid_parameter", f"Statut inconnu : {status!r}.")
    page = search._int(request.args, "page", 1)
    size = search._int(request.args, "size", 20, maximum=100)
    total, items = repository.list_files(status, page, size)
    return jsonify({"total": total, "page": page, "size": size, "files": items})


@bp.get("/api/v1/files/<file_id>")
def get_file(file_id):
    rec = repository.get_file(file_id)
    if rec is None:
        raise ApiError(404, "file_not_found", f"Fichier {file_id!r} introuvable.")
    return jsonify(repository.serialize(rec))


@bp.get("/api/v1/files/<file_id>/status")
def file_status(file_id):
    """Compare le nombre de logs indexés au nombre de lignes du fichier et met le statut à jour."""
    rec = repository.get_file(file_id)
    if rec is None:
        raise ApiError(404, "file_not_found", f"Fichier {file_id!r} introuvable.")
    if rec["status"] != "indexed":
        try:
            count = services.es_client().count(
                index=search.INDEX, query={"term": {"file_id": file_id}})["count"]
        except Exception:  # noqa: BLE001
            log.exception("Erreur Elasticsearch (statut d'ingestion)")
            raise ApiError(503, "elasticsearch_unavailable", "Elasticsearch ne répond pas.") from None
        status = repository.compute_status(rec, count)
        if status != rec["status"] or count != rec.get("nb_logs"):
            rec = repository.update_status(file_id, status, nb_logs=count)
    return jsonify(repository.serialize(rec))


# ---------- Recherche et logs ----------
@bp.get("/search")
@bp.get("/api/v1/logs")
def search_logs():
    return jsonify(search.run_search(request.args))


@bp.get("/api/v1/logs/<log_id>")
def log_detail(log_id):
    return jsonify(search.get_log(log_id))


@bp.get("/api/v1/search/history")
def search_history():
    limit = search._int(request.args, "limit", 20, maximum=100)
    return jsonify({"searches": repository.list_searches(limit)})


# ---------- Statistiques ----------
@bp.get("/api/v1/stats")
def get_stats():
    return jsonify(stats.get_stats())
