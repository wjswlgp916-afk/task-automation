"""웹 대시보드 테스트 (Flask test client)."""

import base64
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytest.importorskip("flask")

from quote_automation import webapp  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "pw123")
    webapp.app.config.update(TESTING=True)
    return webapp.app.test_client()


def _auth(pw="pw123"):
    return {"Authorization": "Basic " + base64.b64encode(f"x:{pw}".encode()).decode()}


def test_requires_password(client):
    assert client.get("/").status_code == 401
    assert client.get("/", headers=_auth("wrong")).status_code == 401
    assert client.get("/", headers=_auth()).status_code == 200


def test_healthz_open(client):
    assert client.get("/healthz").status_code == 200


def test_index_renders_tools(client):
    html = client.get("/", headers=_auth()).get_data(as_text=True)
    assert "견적서 생성기" in html
    assert "K-NSSE" in html and "UICA" in html


def test_generate_combined(client):
    r = client.post("/generate", headers=_auth(), data={
        "university": "서원대학교", "date": "2026-07-21",
        "K_include": "on", "K_grade": "P", "K_addon_1": "on", "K_addon_2": "on",
        "U_include": "on", "U_grade": "B", "combine": "together",
        "fmt_hwp": "on", "fmt_pdf": "on",
    })
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "사백사십만 원정" in html          # 4,400,000
    assert html.count("/download/") == 2      # HWP + PDF (한 장)


def test_generate_separate(client):
    r = client.post("/generate", headers=_auth(), data={
        "university": "OO대학교", "date": "2026-07-21",
        "K_include": "on", "K_grade": "P", "K_addon_2": "on",
        "U_include": "on", "U_grade": "B", "combine": "separate",
        "fmt_hwp": "on", "fmt_pdf": "on",
    })
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert html.count("/download/") == 4      # 두 견적서 × (HWP+PDF)


def test_generate_validation_error(client):
    r = client.post("/generate", headers=_auth(), data={"university": "", "date": ""})
    assert r.status_code == 400
    assert "대학명" in r.get_data(as_text=True)


def test_download_rejects_bad_token(client):
    assert client.get("/download/..%2f..%2fetc/passwd", headers=_auth()).status_code == 404
    assert client.get("/download/zzz/none.hwp", headers=_auth()).status_code == 404
