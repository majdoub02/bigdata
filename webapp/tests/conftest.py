from types import SimpleNamespace

import fakeredis
import mongomock
import pytest

from app import create_app, services


class FakeES:
    """Faux client Elasticsearch : enregistre les appels et renvoie des réponses préparées."""

    def __init__(self):
        self.calls = []
        self.search_result = {"took": 3, "hits": {"total": {"value": 0}, "hits": []}}
        self.count_result = 0
        self.fail = False

    def search(self, **kwargs):
        self.calls.append(("search", kwargs))
        if self.fail:
            raise ConnectionError("Elasticsearch down")
        return self.search_result

    def count(self, **kwargs):
        self.calls.append(("count", kwargs))
        if self.fail:
            raise ConnectionError("Elasticsearch down")
        return {"count": self.count_result}


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    monkeypatch.setenv("ES_URL", "http://fake:9200")
    monkeypatch.setenv("MONGO_URI", "mongodb://fake:27017")
    monkeypatch.setenv("REDIS_URL", "redis://fake:6379/0")
    monkeypatch.setenv("INBOX_DIR", str(tmp_path / "inbox"))
    mongo = mongomock.MongoClient(tz_aware=True)
    db = mongo["seclog_test"]
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    es = FakeES()
    monkeypatch.setattr(services, "mongo_client", lambda: mongo)
    monkeypatch.setattr(services, "mongo_db", lambda: db)
    monkeypatch.setattr(services, "redis_client", lambda: redis_client)
    monkeypatch.setattr(services, "es_client", lambda: es)
    return SimpleNamespace(db=db, redis=redis_client, es=es, inbox=tmp_path / "inbox")


@pytest.fixture
def client(fakes):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


GOOD_CSV = (
    "timestamp,source_ip,username,event_type,status,level,service,message,user_agent,attack_type,resource\n"
    "2026-10-30T09:01:12Z,185.220.101.4,admin,auth,failure,ERROR,auth,Failed password for admin,curl,brute_force,\n"
    "2026-10-30T09:02:00Z,41.226.12.7,m.ben,auth,success,INFO,auth,Login success,Mozilla,,\n"
)
GOOD_NDJSON = (
    '{"timestamp": "2026-10-30T09:01:12Z", "source_ip": "185.220.101.4", "level": "ERROR", "message": "Failed"}\n'
    '{"timestamp": "2026-10-30T09:02:00Z", "source_ip": "41.226.12.7", "level": "INFO", "message": "OK"}\n'
)
