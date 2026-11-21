import io
from datetime import timedelta

from app import repository

from .conftest import GOOD_CSV, GOOD_NDJSON


def upload(client, name, content, route="/upload"):
    data = {"file": (io.BytesIO(content.encode() if isinstance(content, str) else content), name)}
    return client.post(route, data=data, content_type="multipart/form-data")


# ---------- upload ----------
def test_upload_csv_success(client, fakes):
    res = upload(client, "auth.csv", GOOD_CSV)
    body = res.get_json()
    assert res.status_code == 202 and body["status"] == "pending" and body["rows"] == 2
    file_id = body["file_id"]
    assert file_id.startswith("f_") and file_id.endswith("_001")
    saved = fakes.inbox / f"{file_id}.csv"                       # fichier dans le dossier surveillé
    assert saved.read_text().splitlines()[0].startswith("timestamp,source_ip")
    assert not list(fakes.inbox.glob("*.tmp"))                    # pas de fichier temporaire restant
    doc = fakes.db["files"].find_one({"_id": file_id})            # métadonnées dans MongoDB
    assert doc["filename"] == "auth.csv" and doc["status"] == "pending" and doc["rows"] == 2


def test_upload_json_success_and_ids_increment(client, fakes):
    a = upload(client, "a.json", GOOD_NDJSON).get_json()["file_id"]
    b = upload(client, "b.json", GOOD_NDJSON).get_json()["file_id"]
    assert a.endswith("_001") and b.endswith("_002")
    assert (fakes.inbox / f"{b}.json").exists()


def test_upload_without_file(client):
    res = client.post("/upload", data={}, content_type="multipart/form-data")
    assert res.status_code == 400 and res.get_json()["error"]["code"] == "missing_file"


def test_upload_invalid_file_is_rejected_and_nothing_saved(client, fakes):
    res = upload(client, "bad.csv", "timestamp,source_ip\nhier,1.2.3.4\n")
    assert res.status_code == 400 and res.get_json()["error"]["code"] == "invalid_timestamp"
    assert fakes.db["files"].count_documents({}) == 0
    assert not fakes.inbox.exists() or not list(fakes.inbox.iterdir())


def test_upload_unsupported_extension(client):
    res = upload(client, "malware.exe", "abc")
    assert res.status_code == 415


def test_upload_too_large(fakes, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    from app import create_app
    app = create_app()
    res = app.test_client().post("/upload", data={"file": (io.BytesIO(b"x" * (2 * 1024 * 1024)), "big.csv")},
                                 content_type="multipart/form-data")
    assert res.status_code == 413 and res.get_json()["error"]["code"] == "file_too_large"


def test_preview_does_not_save(client, fakes):
    res = upload(client, "auth.csv", GOOD_CSV, route="/upload/preview")
    body = res.get_json()
    assert res.status_code == 200 and body["valid"] and len(body["preview"]) == 2
    assert fakes.db["files"].count_documents({}) == 0


# ---------- fichiers et statut d'ingestion ----------
def test_list_files_and_filter(client, fakes):
    upload(client, "a.csv", GOOD_CSV)
    upload(client, "b.json", GOOD_NDJSON)
    body = client.get("/api/v1/files").get_json()
    assert body["total"] == 2 and {f["filename"] for f in body["files"]} == {"a.csv", "b.json"}
    assert client.get("/api/v1/files?status=indexed").get_json()["total"] == 0
    assert client.get("/api/v1/files?status=bizarre").status_code == 400


def test_file_status_becomes_indexed(client, fakes):
    file_id = upload(client, "a.csv", GOOD_CSV).get_json()["file_id"]
    assert client.get(f"/api/v1/files/{file_id}/status").get_json()["status"] == "pending"
    fakes.es.count_result = 2
    body = client.get(f"/api/v1/files/{file_id}/status").get_json()
    assert body["status"] == "indexed" and body["nb_logs"] == 2
    assert fakes.db["files"].find_one({"_id": file_id})["status"] == "indexed"


def test_file_status_error_when_nothing_indexed_after_a_while(client, fakes):
    file_id = upload(client, "a.csv", GOOD_CSV).get_json()["file_id"]
    old = repository.now() - timedelta(minutes=5)
    fakes.db["files"].update_one({"_id": file_id}, {"$set": {"uploaded_at": old}})
    assert client.get(f"/api/v1/files/{file_id}/status").get_json()["status"] == "error"


def test_unknown_file_is_404(client):
    assert client.get("/api/v1/files/f_inconnu").status_code == 404
    assert client.get("/api/v1/files/f_inconnu/status").status_code == 404


def test_compute_status_rules():
    rec = {"uploaded_at": repository.now(), "rows": 100}
    later = repository.now() + timedelta(minutes=5)
    assert repository.compute_status(rec, 100) == "indexed"
    assert repository.compute_status(rec, 40) == "indexing"
    assert repository.compute_status(rec, 40, at=later) == "partial"
    assert repository.compute_status(rec, 0) == "pending"
    assert repository.compute_status(rec, 0, at=later) == "error"


# ---------- recherche, détail, statistiques ----------
def test_search_route_and_alias(client, fakes):
    fakes.es.search_result = {"took": 1, "hits": {"total": {"value": 1}, "hits": [
        {"_id": "abc", "_index": "logs-security-x", "_source": {"level": "ERROR"}}]}}
    for url in ("/search?q=failed&level=ERROR", "/api/v1/logs?q=failed&level=ERROR"):
        body = client.get(url).get_json()
        assert body["total"] == 1 and body["hits"][0]["id"] == "abc"
    assert client.get("/search?page=0").status_code == 400


def test_log_detail_route(client, fakes):
    fakes.es.search_result = {"hits": {"total": {"value": 0}, "hits": []}}
    assert client.get("/api/v1/logs/zzz").status_code == 404


def test_search_history_route(client, fakes):
    fakes.es.search_result = {"took": 1, "hits": {"total": {"value": 0}, "hits": []}}
    client.get("/search?q=root")
    assert client.get("/api/v1/search/history").get_json()["searches"][0]["query"] == "root"


def test_stats_route(client, fakes):
    fakes.es.search_result = {"hits": {"total": {"value": 20492}}, "aggregations": {
        "today": {"doc_count": 18}, "errors": {"doc_count": 2317},
        "by_level": {"buckets": [{"key": "INFO", "doc_count": 100}]},
        "top_failed_ips": {"ips": {"buckets": [{"key": "185.220.101.4", "doc_count": 120}]}},
        "timeline": {"per_bucket": {"buckets": [{"key": 1, "key_as_string": "2026-11-14T00:00:00.000Z", "doc_count": 7}]}}}}
    upload(client, "a.csv", GOOD_CSV)
    body = client.get("/api/v1/stats").get_json()
    assert body["total_logs"] == 20492 and body["logs_today"] == 18 and body["error_logs"] == 2317
    assert body["files_uploaded"] == 1 and body["top_failed_ips"][0]["ip"] == "185.220.101.4"
    assert body["cached"] is False
    assert client.get("/api/v1/stats").get_json()["cached"] is True   # 2e appel servi par Redis


def test_stats_503_when_elasticsearch_down(client, fakes):
    fakes.es.fail = True
    res = client.get("/api/v1/stats")
    assert res.status_code == 503 and res.get_json()["error"]["code"] == "elasticsearch_unavailable"


def test_unknown_api_route_returns_json_404(client):
    res = client.get("/api/v1/nimporte")
    assert res.status_code == 404 and "error" in res.get_json()
