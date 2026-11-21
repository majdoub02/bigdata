import pytest

from app import repository, search
from app.errors import ApiError


def params(**args):
    return search.parse_params(args)


# ---------- paramètres ----------
def test_defaults():
    p = params()
    assert p["page"] == 1 and p["size"] == 50 and p["sort"] == "@timestamp:desc" and p["q"] is None


def test_level_is_uppercased_and_validated():
    assert params(level="error,critical")["level"] == ["ERROR", "CRITICAL"]
    with pytest.raises(ApiError):
        params(level="verbose")


@pytest.mark.parametrize("args", [{"page": "0"}, {"page": "abc"}, {"size": "101"}, {"source_ip": "999.1.1.1"},
                                  {"from": "hier"}, {"sort": "message:asc"}, {"page": "300", "size": "50"}])
def test_invalid_parameters(args):
    with pytest.raises(ApiError) as exc:
        params(**args)
    assert exc.value.status == 400


def test_to_date_includes_whole_day():
    assert params(to="2026-10-31")["to"] == "2026-10-31||/d"


# ---------- requête Elasticsearch ----------
def test_query_without_filters_is_match_all():
    body = search.build_query(params())
    assert body["query"]["bool"]["must"] == [{"match_all": {}}]
    assert body["from_"] == 0 and body["size"] == 50 and body["track_total_hits"] is True


def test_query_with_all_filters():
    body = search.build_query(params(q="failed password", level="ERROR", service="Auth",
                                     source_ip="1.2.3.4", **{"from": "2026-10-01", "to": "2026-10-31"}, page="3"))
    flt = body["query"]["bool"]["filter"]
    assert {"terms": {"level": ["ERROR"]}} in flt
    assert {"term": {"service": "auth"}} in flt
    assert {"term": {"source_ip": "1.2.3.4"}} in flt
    assert {"range": {"@timestamp": {"gte": "2026-10-01", "lte": "2026-10-31||/d"}}} in flt
    assert body["query"]["bool"]["must"][0]["simple_query_string"]["query"] == "failed password"
    assert body["from_"] == 100


def test_sort_by_level_adds_timestamp_tiebreaker():
    body = search.build_query(params(sort="level:asc"))
    assert len(body["sort"]) == 2 and "level" in body["sort"][0]


# ---------- exécution : ES + cache Redis + historique MongoDB ----------
def es_response(n=2):
    hits = [{"_id": f"id{i}", "_index": "logs-security-2026.10.30",
             "_source": {"@timestamp": "2026-10-30T09:00:00Z", "level": "ERROR", "service": "auth"}}
            for i in range(n)]
    return {"took": 5, "hits": {"total": {"value": 120}, "hits": hits}}


def test_search_formats_results(fakes):
    fakes.es.search_result = es_response()
    res = search.run_search({"q": "failed"})
    assert res["total"] == 120 and res["pages"] == 3 and res["size"] == 50
    assert res["hits"][0]["id"] == "id0" and res["cached"] is False


def test_second_identical_search_uses_redis_cache(fakes):
    fakes.es.search_result = es_response()
    search.run_search({"q": "failed"})
    again = search.run_search({"q": "failed"})
    assert again["cached"] is True
    assert sum(1 for c in fakes.es.calls if c[0] == "search") == 1   # une seule requête ES
    assert fakes.redis.ttl(search.cache_key(params(q="failed"))) > 0


def test_every_search_is_saved_in_history(fakes):
    fakes.es.search_result = es_response()
    search.run_search({"q": "root", "level": "ERROR"})
    doc = fakes.db["search_history"].find_one()
    assert doc["query"] == "root" and doc["filters"]["level"] == ["ERROR"] and doc["results_count"] == 120
    assert repository.list_searches()[0]["query"] == "root"


def test_search_still_works_when_redis_is_down(fakes, monkeypatch):
    class Broken:
        def get(self, *a): raise ConnectionError
        def set(self, *a, **k): raise ConnectionError
    monkeypatch.setattr("app.services.redis_client", lambda: Broken())
    fakes.es.search_result = es_response()
    assert search.run_search({})["total"] == 120


def test_elasticsearch_down_gives_503(fakes):
    fakes.es.fail = True
    with pytest.raises(ApiError) as exc:
        search.run_search({})
    assert exc.value.status == 503


def test_get_log_found_and_not_found(fakes):
    fakes.es.search_result = es_response(1)
    assert search.get_log("id0")["level"] == "ERROR"
    fakes.es.search_result = {"hits": {"total": {"value": 0}, "hits": []}}
    with pytest.raises(ApiError) as exc:
        search.get_log("nope")
    assert exc.value.status == 404
