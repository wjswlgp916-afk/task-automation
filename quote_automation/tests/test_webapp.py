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
    assert "서류 생성기" in html
    assert "K-NSSE" in html and "UICA" in html
    assert "거래명세서" in html


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


def test_guaranty_extra_fields_shown_with_defaults(client):
    html = client.get("/", headers=_auth()).get_data(as_text=True)
    assert "계약보증금 지급각서" in html
    assert 'name="extra_contract_start"' in html
    assert 'value="2027-01-31"' in html      # 계약 종료일 기본값
    assert 'value="2026-09-01"' in html      # 착수일 기본값


def test_guaranty_requires_contract_start(client):
    r = client.post("/generate", headers=_auth(), data={
        "university": "호서대학교", "date": "2026-08-15",
        "K_include": "on", "K_grade": "P",
        "doc_guaranty": "on", "fmt_hwp": "on",
    })
    assert r.status_code == 400
    assert "계약 시작일" in r.get_data(as_text=True)


def test_guaranty_generates_with_dates(client):
    r = client.post("/generate", headers=_auth(), data={
        "university": "호서대학교", "date": "2026-08-15",
        "K_include": "on", "K_grade": "P",
        "doc_guaranty": "on",
        "extra_contract_start": "2026-09-01",
        "extra_contract_end": "2027-01-31",
        "extra_commencement": "2026-09-01",
        "fmt_hwp": "on", "fmt_pdf": "on",
    })
    assert r.status_code == 200
    assert r.get_data(as_text=True).count("/download/") == 2   # 각서 HWP+PDF


def test_inspection_extra_fields_shown_no_defaults(client):
    html = client.get("/", headers=_auth()).get_data(as_text=True)
    assert "검수확인서" in html
    assert 'name="extra_work_start"' in html
    assert 'name="extra_work_end"' in html


def test_inspection_requires_work_dates(client):
    r = client.post("/generate", headers=_auth(), data={
        "university": "호서대학교", "date": "2026-08-15",
        "K_include": "on", "K_grade": "P", "K_addon_2": "on",
        "doc_inspection": "on", "fmt_hwp": "on",
    })
    assert r.status_code == 400
    assert "작업 시작일" in r.get_data(as_text=True)


def test_inspection_generates_with_dates(client):
    r = client.post("/generate", headers=_auth(), data={
        "university": "호서대학교", "date": "2026-08-15",
        "K_include": "on", "K_grade": "P", "K_addon_2": "on",
        "doc_inspection": "on",
        "extra_work_start": "2026-09-01",
        "extra_work_end": "2026-12-31",
        "fmt_hwp": "on", "fmt_pdf": "on",
    })
    assert r.status_code == 200
    assert r.get_data(as_text=True).count("/download/") == 2   # 검수확인서 HWP+PDF


def test_inspection_basic_only_shows_error(client):
    r = client.post("/generate", headers=_auth(), data={
        "university": "호서대학교", "date": "2026-08-15",
        "K_include": "on", "K_grade": "B",
        "doc_inspection": "on",
        "extra_work_start": "2026-09-01",
        "extra_work_end": "2026-12-31",
        "fmt_hwp": "on",
    })
    assert r.status_code == 400
    assert "베이직" in r.get_data(as_text=True)


def test_completion_report_extra_fields_shown_with_defaults(client):
    html = client.get("/", headers=_auth()).get_data(as_text=True)
    assert "완료계" in html
    assert 'name="extra_contract_date"' in html
    assert 'value="2026-09-01"' in html      # 계약년월일·착수년월일 공통 기본값
    assert 'value="2027-01-31"' in html      # 완료기한 기본값
    assert 'value="2026-12-18"' in html      # 완료년월일 기본값


def test_completion_report_defaults_contract_date_without_input(client):
    """계약년월일은 기본값(9월 1일)이 있어, 비워두고 제출해도 통과해야 한다."""
    r = client.post("/generate", headers=_auth(), data={
        "university": "호서대학교", "date": "2026-08-15",
        "K_include": "on", "K_grade": "P",
        "doc_completion_report": "on", "fmt_hwp": "on",
    })
    assert r.status_code == 200, r.get_data(as_text=True)


def test_completion_report_generates_with_dates(client):
    r = client.post("/generate", headers=_auth(), data={
        "university": "호서대학교", "date": "2026-08-15",
        "K_include": "on", "K_grade": "P",
        "doc_completion_report": "on",
        "extra_contract_date": "2026-03-10",
        "fmt_hwp": "on", "fmt_pdf": "on",
    })
    assert r.status_code == 200
    assert r.get_data(as_text=True).count("/download/") == 2   # 완료계 HWP+PDF


def test_extra_field_inputs_not_duplicated_across_doc_types(client):
    """완료계·착수계(계약년월일·완료기한)나 계약서·서약서(자문 시작일)처럼
    여러 서류가 같은 추가 입력 key 를 공유할 때, 입력칸(<input name=...>)이
    서류마다 따로 생기면 안 된다 — 같은 name 이 여러 개면 폼 제출 시 값이
    엉뚱한(숨겨진) 입력칸에서 읽혀, 사용자가 값을 채워도 계속 '값을
    입력하세요' 오류가 나는 버그가 실제로 있었다."""
    html = client.get("/", headers=_auth()).get_data(as_text=True)
    for key in ("contract_date", "completion_deadline", "period_start"):
        assert html.count(f'name="extra_{key}"') == 1, f"'{key}' 입력칸이 중복됨"


def test_commencement_alone_generates_without_completion_report_checked(client):
    """착수계만 체크하고(완료계는 체크 안 함) 계약년월일을 채우면 정상
    생성돼야 한다 — 완료계용 숨은 입력칸과 이름이 겹쳐 값이 뒤섞이던
    회귀 버그 재현 테스트."""
    r = client.post("/generate", headers=_auth(), data={
        "university": "서울여자대학교", "date": "2026-07-24",
        "K_include": "on", "K_grade": "P",
        "doc_quote": "on", "doc_commencement": "on",
        "extra_contract_date": "2026-09-01",
        "fmt_hwp": "on", "fmt_pdf": "on",
    })
    body = r.get_data(as_text=True)
    assert r.status_code == 200, body
    assert "착수계" in body
    assert "값을 입력하세요" not in body


def test_generate_multiple_doc_types(client):
    # 견적서(HWP+PDF) + 거래명세서(HWP+PDF) 를 동시에 선택
    r = client.post("/generate", headers=_auth(), data={
        "university": "서원대학교", "date": "2026-07-21",
        "K_include": "on", "K_grade": "P",
        "doc_quote": "on", "doc_transaction_statement": "on",
        "fmt_hwp": "on", "fmt_pdf": "on",
    })
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert html.count("/download/") == 4   # (견적서 + 거래명세서) × (HWP+PDF)
    assert "거래명세서" in html
    assert "견적서" in html


def test_generate_validation_error(client):
    r = client.post("/generate", headers=_auth(), data={"university": "", "date": ""})
    assert r.status_code == 400
    assert "대학명" in r.get_data(as_text=True)


def test_download_rejects_bad_token(client):
    assert client.get("/download/..%2f..%2fetc/passwd", headers=_auth()).status_code == 404
    assert client.get("/download/zzz/none.hwp", headers=_auth()).status_code == 404


def test_generate_get_redirects_to_index_instead_of_405(client):
    # 브라우저가 예전에 방문한 /generate 주소를 자동완성 등으로 GET 하면
    # 405 대신 입력 화면(/)으로 되돌려야 한다.
    r = client.get("/generate", headers=_auth())
    assert r.status_code == 302
    assert r.headers["Location"] == "/"
