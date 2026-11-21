"""Test de bout en bout : upload -> Logstash -> Elasticsearch -> recherche.

Lancer (docker compose up -d doit tourner) :
    RUN_INTEGRATION=1 pytest -m integration            (PowerShell : $env:RUN_INTEGRATION=1; pytest -m integration)
"""
import os
import time
import uuid

import pytest
import requests

BASE = os.getenv("BASE_URL", "http://localhost:8000")
pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="RUN_INTEGRATION=1 requis")]


def make_csv(marker, n=20):
    lines = ["timestamp,source_ip,username,event_type,status,level,service,message,user_agent,attack_type,resource"]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for i in range(n):
        lines.append(f"{now},41.226.12.{i + 1},tester,auth,failure,ERROR,auth,{marker} failure {i},pytest,,")
    return "\n".join(lines) + "\n"


def test_upload_then_search():
    marker = "integ" + uuid.uuid4().hex[:8]
    res = requests.post(f"{BASE}/upload", files={"file": ("integration.csv", make_csv(marker))}, timeout=30)
    assert res.status_code == 202, res.text
    file_id = res.json()["file_id"]

    status = None
    for _ in range(40):                                   # jusqu'à ~60 s
        status = requests.get(f"{BASE}/api/v1/files/{file_id}/status", timeout=10).json()
        if status["status"] == "indexed":
            break
        time.sleep(1.5)
    assert status["status"] == "indexed" and status["nb_logs"] == 20, status

    found = requests.get(f"{BASE}/search", params={"q": marker}, timeout=10).json()
    assert found["total"] == 20

    files = requests.get(f"{BASE}/api/v1/files", timeout=10).json()
    assert any(f["file_id"] == file_id for f in files["files"])
