"""문서 종류(거래명세서 등) 등록·생성 검증 테스트."""

import struct
import sys
import zlib
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quote_automation.documents import (  # noqa: E402
    DOCUMENT_TYPES, template_path, coerce_extra, extra_fields_for,
)
from quote_automation.engine import build_quote  # noqa: E402
from quote_automation.generator import generate, GeneratedFile  # noqa: E402
from quote_automation.hwp_writer import (  # noqa: E402
    render_hwp, parse_records, text_of, _split_cells, TABLE,
    PARA_HEADER, PARA_TEXT, PARA_CHAR_SHAPE, PARA_LINE_SEG, PARA_RANGE_TAG,
)
from quote_automation import cfbf  # noqa: E402


def _assert_para_header_range_counts_consistent(recs):
    """PARA_HEADER 의 '범위 태그(형광펜) 개수' 필드가 실제 남아있는
    PARA_RANGE_TAG 항목 수와 어긋나면 한글에서 '파일이 손상되었습니다'
    오류가 난다 (실제로 겪은 버그) — strip_highlight_ranges() 가 하이라이트를
    지우면서 이 개수도 함께 줄였는지 확인한다."""
    for i, r in enumerate(recs):
        if r.tag != PARA_HEADER:
            continue
        declared = struct.unpack("<H", r.payload[14:16])[0]
        j = i + 1
        if j < len(recs) and recs[j].tag == PARA_TEXT:
            j += 1
        actual = 0
        while j < len(recs) and recs[j].tag in (PARA_CHAR_SHAPE, PARA_LINE_SEG, PARA_RANGE_TAG):
            if recs[j].tag == PARA_RANGE_TAG:
                actual += len(recs[j].payload) // 12
            j += 1
        assert declared == actual, f"문단 {i}: 선언된 범위 태그 수={declared}, 실제={actual}"


def _guaranty_extra():
    return coerce_extra(["guaranty"], {"contract_start": "2026-09-01"})


def _inspection_extra():
    return coerce_extra(["inspection"],
                         {"work_start": "2026-09-01", "work_end": "2026-12-31"})


def _completion_extra(contract_date="2026-03-10"):
    return coerce_extra(["completion_report"], {"contract_date": contract_date})

ALL_CODES = [
    "K_B", "K_P", "K_P_1", "K_P_2", "K_P_12",
    "U_B", "U_P", "U_P_1",
    "K_P_12+U_P_1", "K_P_2+U_B",
]


def test_transaction_statement_template_registered():
    assert "transaction_statement" in DOCUMENT_TYPES
    doc = DOCUMENT_TYPES["transaction_statement"]
    assert doc.label == "거래명세서"
    assert doc.supports_pdf is True
    path = template_path(doc)
    assert path is not None and path.exists()


@pytest.mark.parametrize("code", ALL_CODES)
def test_transaction_statement_hwp_generates_and_validates(tmp_path, code):
    q = build_quote("호서대학교", code, date(2026, 7, 21))
    doc = DOCUMENT_TYPES["transaction_statement"]
    out = render_hwp(q, tmp_path / "ts.hwp", template=template_path(doc))
    sm = {tuple(p): d for p, d in cfbf.read_streams(str(out))}
    recs = parse_records(zlib.decompress(sm[("BodyText", "Section0")], -15))
    texts = [text_of(r) for r in recs if r.tag == 67]
    joined = " ".join(texts)
    assert "호서대학교  귀중" in joined
    assert "2026년    07월    21일" in joined
    assert f"{q.grand_total:,}" in joined
    # 견적서 제목이 아니라 거래명세서 제목이어야 한다
    assert "거 래 명 세 서" in joined
    assert "견 적 서" not in joined
    # 원본 양식에 남아있던 형광펜(하이라이트) 자국도 산출물엔 없어야 한다
    assert not any(r.tag == 70 for r in recs)
    _assert_para_header_range_counts_consistent(recs)


def test_generate_produces_both_doc_types_with_correct_formats(tmp_path):
    files = generate(
        "서원대학교", "K_P_12+U_B", tmp_path, date(2026, 7, 21),
        formats=["hwp", "pdf"], doc_types=["quote", "transaction_statement"],
    )
    by_label = {}
    for gf in files:
        assert isinstance(gf, GeneratedFile)
        by_label.setdefault(gf.doc_label, []).append(gf.path.suffix)

    assert sorted(by_label["견적서"]) == [".hwp", ".pdf"]
    assert sorted(by_label["거래명세서"]) == [".hwp", ".pdf"]
    for gf in files:
        assert gf.path.is_file()


def test_transaction_statement_pdf_title_and_no_greeting(tmp_path):
    pytest.importorskip("pypdfium2")
    import pypdfium2 as pdfium

    q = build_quote("호서대학교", "K_P_2+U_B", date(2025, 8, 19))
    doc = DOCUMENT_TYPES["transaction_statement"]
    out = doc.render_pdf(q, tmp_path / "ts.pdf")

    pdf = pdfium.PdfDocument(str(out))
    text = pdf[0].get_textpage().get_text_range()
    assert "거 래 명 세 서" in text
    assert "견 적 서" not in text
    assert "아래와 같이 견적합니다" not in text   # 원본에 없는 문구는 PDF에도 없어야 함
    assert "3,300,000" in text


def test_invoice_registered():
    assert "invoice" in DOCUMENT_TYPES
    doc = DOCUMENT_TYPES["invoice"]
    assert doc.label == "대금청구서"
    assert doc.supports_pdf is True
    path = template_path(doc)
    assert path is not None and path.exists()


@pytest.mark.parametrize("code,expect_subject", [
    ("K_P_12", "학부교육의 질과 성과 진단 및 분석"),   # K 단독: 기본값 유지
    ("K_B", "학부교육의 질과 성과 진단 및 분석"),
    ("U_P_1", "대학 혁신역량 진단 및 분석"),           # U 단독: 건명 변경
    ("U_B", "대학 혁신역량 진단 및 분석"),
    ("K_P_12+U_P_1", "학부교육의 질과 성과 진단 및 분석"),  # 결합: 기본값 유지
])
def test_invoice_subject_depends_on_tool(tmp_path, code, expect_subject):
    doc = DOCUMENT_TYPES["invoice"]
    q = build_quote("호서대학교", code, date(2026, 7, 21))
    out = doc.render_hwp(q, tmp_path / "inv.hwp", None)
    sm = {tuple(p): d for p, d in cfbf.read_streams(str(out))}
    recs = parse_records(zlib.decompress(sm[("BodyText", "Section0")], -15))
    texts = [text_of(r) for r in recs if r.tag == 67]
    joined = " ".join(texts)
    assert f"건 명 : {expect_subject}" in joined
    assert f"{q.grand_total:,}" in joined
    assert "호서대학교 총장 귀하" in joined
    assert "2026. 07. 21." in joined


def test_invoice_amount_matches_quote_style(tmp_path):
    doc = DOCUMENT_TYPES["invoice"]
    q = build_quote("호서대학교", "K_P_2+U_B", date(2025, 8, 19))  # grand_total 3,300,000
    out = doc.render_hwp(q, tmp_path / "inv.hwp", None)
    sm = {tuple(p): d for p, d in cfbf.read_streams(str(out))}
    recs = parse_records(zlib.decompress(sm[("BodyText", "Section0")], -15))
    texts = [text_of(r) for r in recs if r.tag == 67]
    joined = " ".join(texts)
    # 대금청구서 고유 스타일: '삼백삼십만원' (원정 없음, 붙여쓰기)
    assert "삼백삼십만원" in joined
    assert "삼백삼십만 원정" not in joined


def test_invoice_pdf_renders(tmp_path):
    pytest.importorskip("pypdfium2")
    import pypdfium2 as pdfium

    doc = DOCUMENT_TYPES["invoice"]
    q = build_quote("호서대학교", "U_P_1", date(2026, 7, 21))
    out = doc.render_pdf(q, tmp_path / "inv.pdf")
    pdf = pdfium.PdfDocument(str(out))
    text = pdf[0].get_textpage().get_text_range()
    assert "대 금 청 구 서" in text
    assert "대학 혁신역량 진단 및 분석" in text
    assert "3,300,000" in text
    assert "호서대학교 총장 귀하" in text


def test_guaranty_registered_with_extra_fields():
    assert "guaranty" in DOCUMENT_TYPES
    doc = DOCUMENT_TYPES["guaranty"]
    assert doc.label == "계약보증금 지급각서"
    assert doc.supports_pdf is True
    keys = [f.key for f in doc.extra_fields]
    assert keys == ["contract_start", "contract_end", "commencement"]
    assert doc.extra_fields[0].required is True     # 계약 시작일 필수
    assert doc.extra_fields[1].default == "2027-01-31"
    assert doc.extra_fields[2].default == "2026-09-01"


def test_coerce_extra_applies_defaults_and_requires_start():
    import pytest as _pytest
    with _pytest.raises(ValueError):
        coerce_extra(["guaranty"], {})              # 계약 시작일 없음
    got = coerce_extra(["guaranty"], {"contract_start": "2026-09-05"})
    assert got["contract_start"] == date(2026, 9, 5)
    assert got["contract_end"] == date(2027, 1, 31)   # 기본값
    assert got["commencement"] == date(2026, 9, 1)    # 기본값


@pytest.mark.parametrize("code,expect_subject", [
    ("K_P_12", "학부교육의 질과 성과 진단 및 분석"),
    ("U_P_1", "대학 혁신역량 진단 및 분석"),
    ("K_P_12+U_P_1", "학부교육의 질과 성과 진단 및 분석"),
])
def test_guaranty_hwp_fields(tmp_path, code, expect_subject):
    doc = DOCUMENT_TYPES["guaranty"]
    q = build_quote("호서대학교", code, date(2026, 8, 15))
    out = doc.render_hwp(q, tmp_path / "g.hwp", None, extra=_guaranty_extra())
    sm = {tuple(p): d for p, d in cfbf.read_streams(str(out))}
    recs = parse_records(zlib.decompress(sm[("BodyText", "Section0")], -15))
    joined = " ".join(text_of(r) for r in recs if r.tag == 67)
    assert f"계  약  명 : {expect_subject}" in joined
    assert f"{q.grand_total:,}" in joined               # 계약금액
    assert f"{round(q.grand_total * 0.1):,}" in joined   # 계약보증금(10%)
    assert "2026. 09. 01 ∼ 2027. 01. 31 (착수일: 2026년 9월 1일)" in joined
    assert "2026년  8월  15일" in joined                 # 발급일자(착수일과 다름)
    assert "호서대학교 총장 귀하" in joined


def test_guaranty_missing_dates_raises(tmp_path):
    doc = DOCUMENT_TYPES["guaranty"]
    q = build_quote("호서대학교", "K_P_12", date(2026, 8, 15))
    with pytest.raises(Exception):
        doc.render_hwp(q, tmp_path / "g.hwp", None, extra=None)


def test_guaranty_pdf_renders(tmp_path):
    pytest.importorskip("pypdfium2")
    import pypdfium2 as pdfium
    doc = DOCUMENT_TYPES["guaranty"]
    q = build_quote("호서대학교", "K_P_12+U_P_1", date(2026, 8, 15))
    out = doc.render_pdf(q, tmp_path / "g.pdf", extra=_guaranty_extra())
    text = pdfium.PdfDocument(str(out))[0].get_textpage().get_text_range()
    assert "계약보증금 지급각서" in text
    assert "7,700,000" in text and "770,000" in text
    assert "호서대학교 총장 귀하" in text


def test_generate_guaranty_needs_extra(tmp_path):
    # extra 없이 guaranty 생성 시도 -> 예외
    with pytest.raises(Exception):
        generate("호서대학교", "K_P_12", tmp_path, date(2026, 8, 15),
                 doc_types=["guaranty"], extra=None)
    # extra 주면 성공
    files = generate("호서대학교", "K_P_12", tmp_path, date(2026, 8, 15),
                     doc_types=["guaranty"], extra=_guaranty_extra())
    assert len(files) == 2 and all(gf.path.is_file() for gf in files)


def test_generate_unknown_doc_type_raises(tmp_path):
    with pytest.raises(ValueError):
        generate("테스트대학교", "U_P", tmp_path, date(2026, 7, 21),
                 doc_types=["존재하지않는서류"])


def test_quote_template_still_default_when_doctype_not_specified(tmp_path):
    files = generate("테스트대학교", "U_P", tmp_path, date(2026, 7, 21))
    assert len(files) == 2  # 기본값 quote: HWP + PDF
    assert all(gf.doc_label == "견적서" for gf in files)


# --------------------------------------------------------------------------- #
# 검수확인서
# --------------------------------------------------------------------------- #
def test_inspection_registered_with_extra_fields():
    assert "inspection" in DOCUMENT_TYPES
    doc = DOCUMENT_TYPES["inspection"]
    assert doc.label == "검수확인서"
    assert doc.supports_pdf is True
    keys = [f.key for f in doc.extra_fields]
    assert keys == ["work_start", "work_end"]
    assert all(f.required for f in doc.extra_fields)   # 둘 다 필수, 기본값 없음


def _inspection_table_rows(out_path):
    sm = {tuple(p): d for p, d in cfbf.read_streams(str(out_path))}
    recs = parse_records(zlib.decompress(sm[("BodyText", "Section0")], -15))
    ti = [i for i, r in enumerate(recs) if r.tag == TABLE][0]
    level = recs[ti].level
    j = ti + 1
    while j < len(recs) and recs[j].level >= level:
        j += 1
    cells = _split_cells(recs[ti + 1:j], level)
    rows = {}
    for c in cells:
        rows.setdefault(c.row(), []).append(c)
    return recs, rows


@pytest.mark.parametrize("code,expect_subject,expect_rows", [
    # (코드, 기대 용역명, {(row): (colcount, 첫 텍스트)})
    ("K_P_12+U_P_1", "학부교육의 질과 성과 진단 및 분석", 6),  # header+3(K)+2(U)=6
    ("K_P_2", "학부교육의 질과 성과 진단 및 분석", 3),          # header+base+addon2=3
    ("U_P", "대학 혁신역량 진단 및 분석", 2),                   # header+base=2
])
def test_inspection_hwp_table_matches_selection(tmp_path, code, expect_subject, expect_rows):
    doc = DOCUMENT_TYPES["inspection"]
    q = build_quote("호서대학교", code, date(2026, 8, 15))
    out = doc.render_hwp(q, tmp_path / "insp.hwp", None, extra=_inspection_extra())

    recs, rows = _inspection_table_rows(out)
    assert len(rows) == expect_rows

    joined = " ".join(text_of(r) for r in recs if r.tag == 67)
    assert f"용    역    명 : {expect_subject}" in joined
    assert "2026년 9월 1일 ~ 2026년 12월 31일" in joined
    assert "2026년  8월  15일" in joined       # 발급일자(작업기간과 별개)
    assert "호서대학교 귀하" in joined
    # 원본 양식에 남아있던 형광펜(하이라이트) 자국도 산출물엔 없어야 한다
    assert not any(r.tag == 70 for r in recs)
    _assert_para_header_range_counts_consistent(recs)


def test_inspection_hwp_rowspan_matches_addon_count(tmp_path):
    # K_P_2: 단과대학별(addon1)은 빠지고 base+Peer(addon2)만 -> rowspan 2
    doc = DOCUMENT_TYPES["inspection"]
    q = build_quote("호서대학교", "K_P_2", date(2026, 8, 15))
    out = doc.render_hwp(q, tmp_path / "insp.hwp", None, extra=_inspection_extra())
    _, rows = _inspection_table_rows(out)
    label_cell = [c for c in rows[1] if c.col() == 0][0]
    assert label_cell.rowspan() == 2
    assert "단과대학별 비교분석" not in " ".join(c.first_text() for r in rows.values() for c in r)


def test_inspection_addon1_only_skips_addon2(tmp_path):
    doc = DOCUMENT_TYPES["inspection"]
    q = build_quote("호서대학교", "K_P_1", date(2026, 8, 15))
    out = doc.render_hwp(q, tmp_path / "insp.hwp", None, extra=_inspection_extra())
    _, rows = _inspection_table_rows(out)
    texts = [c.first_text() for r in rows.values() for c in r]
    assert "단과대학별 비교분석" in texts
    assert "Peer Benchmarking" not in texts


def test_inspection_basic_only_raises(tmp_path):
    doc = DOCUMENT_TYPES["inspection"]
    q = build_quote("호서대학교", "K_B", date(2026, 8, 15))
    with pytest.raises(Exception, match="베이직"):
        doc.render_hwp(q, tmp_path / "insp.hwp", None, extra=_inspection_extra())


def test_inspection_mixed_basic_and_premium_only_shows_premium(tmp_path):
    # K 베이직 + U 프리미어 결합 -> 표에는 UICA 만 남아야 한다
    doc = DOCUMENT_TYPES["inspection"]
    q = build_quote("호서대학교", "K_B+U_P_1", date(2026, 8, 15))
    out = doc.render_hwp(q, tmp_path / "insp.hwp", None, extra=_inspection_extra())
    _, rows = _inspection_table_rows(out)
    texts = [c.first_text() for r in rows.values() for c in r]
    assert "대학 혁신역량 진단조사(UICA)" in texts
    assert "학부교육 실태조사(K-NSSE)" not in texts


def test_inspection_missing_work_dates_raises(tmp_path):
    doc = DOCUMENT_TYPES["inspection"]
    q = build_quote("호서대학교", "K_P_12", date(2026, 8, 15))
    with pytest.raises(Exception):
        doc.render_hwp(q, tmp_path / "insp.hwp", None, extra=None)


def test_inspection_pdf_renders(tmp_path):
    pytest.importorskip("pypdfium2")
    import pypdfium2 as pdfium
    doc = DOCUMENT_TYPES["inspection"]
    q = build_quote("호서대학교", "K_P_12+U_P_1", date(2026, 8, 15))
    out = doc.render_pdf(q, tmp_path / "insp.pdf", extra=_inspection_extra())
    text = pdfium.PdfDocument(str(out))[0].get_textpage().get_text_range()
    assert "검 수 확 인 서" in text
    assert "단과대학별 비교분석" in text
    assert "Peer Benchmarking" in text
    assert "호서대학교 귀하" in text


def test_generate_inspection_needs_extra(tmp_path):
    with pytest.raises(Exception):
        generate("호서대학교", "K_P_12", tmp_path, date(2026, 8, 15),
                 doc_types=["inspection"], extra=None)
    files = generate("호서대학교", "K_P_12", tmp_path, date(2026, 8, 15),
                     doc_types=["inspection"], extra=_inspection_extra())
    assert len(files) == 2 and all(gf.path.is_file() for gf in files)


# --------------------------------------------------------------------------- #
# 완료계
# --------------------------------------------------------------------------- #
def test_completion_report_registered_with_extra_fields():
    assert "completion_report" in DOCUMENT_TYPES
    doc = DOCUMENT_TYPES["completion_report"]
    assert doc.label == "완료계"
    assert doc.supports_pdf is True
    keys = [f.key for f in doc.extra_fields]
    assert keys == ["contract_date", "commencement_date", "completion_deadline", "completion_date"]
    assert doc.extra_fields[0].required is True     # 계약년월일: 필수, 기본값 없음
    assert doc.extra_fields[1].default == "2026-09-01"
    assert doc.extra_fields[2].default == "2027-01-31"
    assert doc.extra_fields[3].default == "2026-12-18"


def test_coerce_extra_completion_report_applies_defaults_and_requires_contract_date():
    with pytest.raises(ValueError):
        coerce_extra(["completion_report"], {})
    got = coerce_extra(["completion_report"], {"contract_date": "2026-04-01"})
    assert got["contract_date"] == date(2026, 4, 1)
    assert got["commencement_date"] == date(2026, 9, 1)
    assert got["completion_deadline"] == date(2027, 1, 31)
    assert got["completion_date"] == date(2026, 12, 18)


@pytest.mark.parametrize("code,expect_subject", [
    ("K_P_12", "학부교육의 질과 성과 진단 및 분석"),
    ("U_P_1", "대학 혁신역량 진단 및 분석"),
    ("K_P_12+U_P_1", "학부교육의 질과 성과 진단 및 분석"),
])
def test_completion_report_hwp_fields(tmp_path, code, expect_subject):
    doc = DOCUMENT_TYPES["completion_report"]
    q = build_quote("호서대학교", code, date(2026, 8, 15))
    out = doc.render_hwp(q, tmp_path / "c.hwp", None, extra=_completion_extra())
    sm = {tuple(p): d for p, d in cfbf.read_streams(str(out))}
    recs = parse_records(zlib.decompress(sm[("BodyText", "Section0")], -15))
    joined = " ".join(text_of(r) for r in recs if r.tag == 67)
    assert f"용    역    명 : {expect_subject}" in joined
    assert f"{q.grand_total // 10000}만 원(￦ {q.grand_total:,} )" in joined
    assert "계 약 년 월 일 : 2026년   03월   10일" in joined
    assert "착 수 년 월 일 : 2026년   09월   01일" in joined     # 기본값
    assert "완  료  기  한 : 2027년   01월   31일" in joined       # 기본값
    assert "완 료 년 월 일 : 2026년   12월   18일" in joined       # 기본값
    assert "2026년   08월  15일" in joined                        # 발급일자(계약일과 별개)
    assert "호서대학교 귀하" in joined


def test_completion_report_no_leftover_memo_controls(tmp_path):
    doc = DOCUMENT_TYPES["completion_report"]
    q = build_quote("호서대학교", "K_P_12", date(2026, 8, 15))
    out = doc.render_hwp(q, tmp_path / "c.hwp", None, extra=_completion_extra())
    sm = {tuple(p): d for p, d in cfbf.read_streams(str(out))}
    recs = parse_records(zlib.decompress(sm[("BodyText", "Section0")], -15))
    assert not any(r.tag == 71 and r.payload[:4] == b"knu%" for r in recs)
    assert not any(r.tag == 93 for r in recs)


def test_completion_report_no_leftover_highlight(tmp_path):
    # 원본 완료계 양식은 날짜 자리들에 형광펜(노란 하이라이트)이 칠해져
    # 있었다 (작성자가 잊지 않으려고 표시한 것) — 산출물에는 남으면 안 됨.
    doc = DOCUMENT_TYPES["completion_report"]
    q = build_quote("호서대학교", "K_P_12", date(2026, 8, 15))
    out = doc.render_hwp(q, tmp_path / "c.hwp", None, extra=_completion_extra())
    sm = {tuple(p): d for p, d in cfbf.read_streams(str(out))}
    recs = parse_records(zlib.decompress(sm[("BodyText", "Section0")], -15))
    assert not any(r.tag == 70 for r in recs)
    _assert_para_header_range_counts_consistent(recs)


def test_completion_report_missing_contract_date_raises(tmp_path):
    doc = DOCUMENT_TYPES["completion_report"]
    q = build_quote("호서대학교", "K_P_12", date(2026, 8, 15))
    with pytest.raises(Exception):
        doc.render_hwp(q, tmp_path / "c.hwp", None, extra=None)


def test_completion_report_pdf_renders(tmp_path):
    pytest.importorskip("pypdfium2")
    import pypdfium2 as pdfium
    doc = DOCUMENT_TYPES["completion_report"]
    q = build_quote("호서대학교", "K_P_12+U_P_1", date(2026, 8, 15))
    out = doc.render_pdf(q, tmp_path / "c.pdf", extra=_completion_extra())
    text = pdfium.PdfDocument(str(out))[0].get_textpage().get_text_range()
    assert "완 료 계" in text
    assert "7,700,000" in text
    assert "호서대학교 총장 귀하" in text


def test_generate_completion_report_needs_extra(tmp_path):
    with pytest.raises(Exception):
        generate("호서대학교", "K_P_12", tmp_path, date(2026, 8, 15),
                 doc_types=["completion_report"], extra=None)
    files = generate("호서대학교", "K_P_12", tmp_path, date(2026, 8, 15),
                     doc_types=["completion_report"], extra=_completion_extra())
    assert len(files) == 2 and all(gf.path.is_file() for gf in files)


# --------------------------------------------------------------------------- #
# 자문 계약서 (HWP 전용, 등급별 자동 조정)
# --------------------------------------------------------------------------- #
def _contract_extra(**over):
    return coerce_extra(["contract"], dict(over))


def _contract_texts(out_path):
    sm = {tuple(p): d for p, d in cfbf.read_streams(str(out_path))}
    recs = parse_records(zlib.decompress(sm[("BodyText", "Section0")], -15))
    return recs, "\n".join(text_of(r) for r in recs if r.tag == 67)


_EIGHT_WIDE = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23}


def _assert_contract_structure_ok(recs):
    """계약서 정상 파일 불변식(한글에서 손상 없이 열리는 최소 조건).

    계약서는 실무 안내 메모(인쇄되지 않는 코멘트)를 그대로 두되, 특이사항에
    걸려있던 인라인 메모 2개만 정확히 제거한다. 그리고 표에서 문단을 지운
    행은 '높이 재계산' 속성을 켠다. 손상 없이 열리려면:
      * 특이사항 문단에 인라인 메모 마커가 남지 않아야 하고,
      * 남은 메모는 컨트롤(knu%)·인라인 마커·MEMO_LIST 개수가 서로 정합해야
        하며(짝 없는 마커 = 손상),
      * 필드 시작/끝 마커(0x03/0x04) 총 개수가 균형을 이뤄야 하고,
      * 자문범위 행의 칸들에 높이 재계산 비트(0x05000000)가 켜져 있어야 하며,
      * 글자모양(CHAR_SHAPE) 위치가 텍스트 길이를 넘지 않고 개수 필드와 정합,
      * PARA_HEADER 의 범위 태그 수도 정합해야 한다.
    """
    import struct as _s
    # 메모 정합: 인라인 메모 ID 집합 == CTRL_HEADER ID 집합 == MEMO_LIST ID 집합
    inline_ids, notes_has_marker = [], False
    begin = end = 0
    for r in recs:
        if r.tag != 67:
            continue
        u = _s.unpack(f"<{len(r.payload) // 2}H", r.payload)
        begin += sum(1 for c in u if c == 0x03)
        end += sum(1 for c in u if c == 0x04)
        is_notes = "분석 결과의 타당성" in text_of(r)
        k = 0
        while k < len(u):
            if u[k] == 0x04 and k + 5 < len(u) and u[k + 1] == 0x6D65:
                inline_ids.append(u[k + 5])
                if is_notes:
                    notes_has_marker = True
                k += 8
            elif u[k] in (0x03,) and k + 1 < len(u) and u[k + 1] == 0x6D65:
                if is_notes:
                    notes_has_marker = True
                k += 8
            elif u[k] in _EIGHT_WIDE:
                k += 8
            else:
                k += 1
    ctrl_ids = [_s.unpack("<I", r.payload[-4:])[0] for r in recs
                if r.tag == 71 and r.payload[:4] == b"knu%"]
    memo_ids = [_s.unpack("<I", r.payload[0:4])[0] for r in recs if r.tag == 93]
    assert not notes_has_marker, "특이사항에 인라인 메모 잔존"
    assert sorted(set(inline_ids)) == sorted(ctrl_ids) == sorted(memo_ids), \
        f"메모 정합 깨짐 inline={sorted(set(inline_ids))} ctrl={sorted(ctrl_ids)} memo={sorted(memo_ids)}"
    assert begin == end, f"필드 마커 불균형(begin={begin}, end={end}) = 파일 손상"

    # 자문범위 행의 칸들에 높이 재계산 비트가 켜져 있어야 한다
    from quote_automation import contract_writer as _cw
    li, _, lvl = _cw._cell_region(recs, _cw._SCOPE_ANCHOR)
    srow = _s.unpack("<H", recs[li].payload[10:12])[0]
    for r in recs:
        if r.tag == 72 and r.level == lvl and len(r.payload) >= 24:
            if _s.unpack("<H", r.payload[10:12])[0] == srow:
                fl = _s.unpack("<I", r.payload[4:8])[0]
                assert (fl & 0x05000000) == 0x05000000, "자문범위 행 높이재계산 비트 미설정"

    # 셀 안에서 문단을 지워 재구성한 곳(자문범위·제공자료)마다, 마지막으로
    # 남은 문단에만 '마지막 문단' 비트(0x80000000)가 켜져 있어야 한다.
    # 이 비트가 옛 위치에 남으면(또는 새 마지막 문단에 없으면) 한글이
    # "파일이 손상되었습니다" 로 판정한다(실측으로 확인한 근본 원인).
    for anchor in (_cw._SCOPE_ANCHOR, _cw._DELIV_ANCHOR):
        li2, end2, lvl2 = _cw._cell_region(recs, anchor)
        last_idx = None
        highbit_count = 0
        j = li2 + 1
        while j < end2:
            if recs[j].tag == 66 and recs[j].level == lvl2:
                v = _s.unpack("<I", recs[j].payload[0:4])[0]
                if v & 0x80000000:
                    highbit_count += 1
                last_idx = j
            j += 1
        assert highbit_count == 1, f"'마지막 문단' 비트 개수 이상({anchor}): {highbit_count}"
        last_val = _s.unpack("<I", recs[last_idx].payload[0:4])[0]
        assert last_val & 0x80000000, f"실제 마지막 문단에 비트 없음({anchor})"

    # 글자모양 위치가 텍스트 길이를 넘지 않고 개수 필드와 일치해야 한다
    for i, r in enumerate(recs):
        if r.tag != 66:
            continue
        txt = cs = None
        for j in range(i + 1, min(i + 8, len(recs))):
            if recs[j].tag == 67 and txt is None:
                txt = recs[j]
            if recs[j].tag == 68 and cs is None:
                cs = recs[j]
            if recs[j].tag == 66:
                break
        tlen = len(txt.payload) // 2 if txt else 0
        declared = _s.unpack("<H", r.payload[12:14])[0]
        if cs is not None and tlen > 0:
            n = len(cs.payload) // 8
            assert n == declared, f"글자모양 개수 불일치 para {i}"
            for k in range(n):
                pos = _s.unpack_from("<II", cs.payload, k * 8)[0]
                assert pos < tlen, f"글자모양 위치 범위초과 para {i}: {pos}>={tlen}"
    _assert_para_header_range_counts_consistent(recs)


def test_contract_registered_hwp_only():
    assert "contract" in DOCUMENT_TYPES
    doc = DOCUMENT_TYPES["contract"]
    assert doc.label == "자문 계약서"
    assert doc.supports_pdf is False            # 계약서는 HWP만
    assert template_path(doc).exists()
    keys = [f.key for f in doc.extra_fields]
    # 계약체결일은 대학이 직접 기입하는 자리라 입력 항목으로 받지 않는다
    assert "contract_date" not in keys
    assert keys[:3] == ["payment_due", "period_start", "period_end"]


def test_contract_coerce_defaults_no_required_fields():
    # 계약체결일을 요구하지 않으므로 빈 입력으로도 기본값이 채워진다
    got = coerce_extra(["contract"], {})
    assert got["payment_due"] == date(2027, 2, 13)
    assert got["period_start"] == date(2026, 9, 1)
    assert got["period_end"] == date(2027, 1, 31)


def test_contract_kp1_up_combined(tmp_path):
    """한성대 K_P_1+U_P: 계약명 기본값, 금액, K는 부가1까지 U는 기본만, 설문기준 엑셀조회."""
    doc = DOCUMENT_TYPES["contract"]
    q = build_quote("한성대학교", "K_P_1+U_P", date(2026, 7, 23))
    out = doc.render_hwp(q, tmp_path / "c.hwp", None, extra=_contract_extra())
    recs, joined = _contract_texts(out)
    # 계약명: K+U 결합 -> 기본값 유지
    assert "학부교육의 질과 성과 진단 및 분석" in joined
    # 금액: 5,500,000(포함) / 5,000,000(별도)
    assert "5,500,000" in joined and "5,000,000" in joined
    # 자문범위: K 1~3 + 단과대학별(부가1), U 1~3, Peer 없음
    assert "1. 학부교육 실태조사(K-NSSE)" in joined
    assert "4) (단과대학별 분석)" in joined
    assert "2. 대학 혁신역량 진단조사(UICA)" in joined
    assert "Peer Benchmarking" not in joined
    # 설문기준: 한성대 엑셀값 200/50/50
    assert "재학생 200명 이상 교수 50명 이상, 직원 50명 이상" in joined
    # 대학명 치환 완료
    assert "OO대학교" not in joined
    # 계약체결일은 대학이 직접 기입하는 자리라 placeholder 그대로 남는다
    assert "2026년 0월 0일" in joined
    assert "2026. 0. 0." in joined
    # 정상 파일 불변식: 메모는 실제 배포 양식처럼 그대로 두되(손상 방지),
    # 특이사항에 걸려있던 인라인 메모 2개만 정확히 제거된다.
    _assert_contract_structure_ok(recs)
    _assert_para_header_range_counts_consistent(recs)


def test_contract_uica_only_subject_and_notes(tmp_path):
    doc = DOCUMENT_TYPES["contract"]
    q = build_quote("한성대학교", "U_P_1", date(2026, 7, 23))
    _, joined = _contract_texts(doc.render_hwp(q, tmp_path / "c.hwp", None, extra=_contract_extra()))
    # U 단독 -> 계약명 변경
    assert "대학 혁신역량 진단 및 분석" in joined
    assert "학부교육의 질과 성과" not in joined
    # UICA 섹션이 "1." 로 재번호, Peer(부가1) 포함
    assert "1. 대학 혁신역량 진단조사(UICA)" in joined
    assert "4) (Peer Benchmarking)" in joined
    # K 관련 문구 없음, 특이사항엔 재학생 빠지고 교수/직원만
    assert "학부교육 실태조사(K-NSSE)" not in joined
    assert "재학생" not in joined
    assert "교수 50명 이상, 직원 50명 이상" in joined
    # 금액 U_P_1 = 3,300,000 / 3,000,000
    assert "3,300,000" in joined and "3,000,000" in joined


def test_contract_uica_basic_adjusts_scope_and_report(tmp_path):
    """K_P_12+U_B: UICA(베이직)은 '대학 간 비교' 한 줄만, 보고서는 excel 파일."""
    doc = DOCUMENT_TYPES["contract"]
    q = build_quote("한성대학교", "K_P_12+U_B", date(2026, 7, 23))
    _, joined = _contract_texts(doc.render_hwp(q, tmp_path / "c.hwp", None, extra=_contract_extra()))
    # K 프리미어 전체 (부가1·2)
    assert "4) (단과대학별 분석)" in joined and "5) (Peer Benchmarking)" in joined
    # UICA 베이직: 대학 간 비교만, 대학 내/성장분석/Peer 없음(UICA쪽)
    assert "1) (대학 간 비교) 한성대학교 교수, 직원" in joined
    assert "교수, 직원의 보직경험별" not in joined      # UICA 대학 내 비교 없음
    # UICA 제공자료 보고서 = excel 파일
    assert "대학별 보고서(excel 파일)" in joined
    # 금액 4,400,000(K_P_12만, UICA B=0) / 4,000,000
    assert "4,400,000" in joined and "4,000,000" in joined
    # 특이사항엔 UICA(교수/직원)도 여전히 포함
    assert "재학생 200명 이상 교수 50명 이상, 직원 50명 이상" in joined


def test_contract_basic_only_rejected(tmp_path):
    doc = DOCUMENT_TYPES["contract"]
    for code in ["K_B", "U_B", "K_B+U_B"]:
        q = build_quote("한성대학교", code, date(2026, 7, 23))
        with pytest.raises(Exception, match="베이직"):
            doc.render_hwp(q, tmp_path / "c.hwp", None, extra=_contract_extra())


def test_contract_survey_override(tmp_path):
    doc = DOCUMENT_TYPES["contract"]
    q = build_quote("한성대학교", "K_P_1+U_P", date(2026, 7, 23))
    extra = _contract_extra(k_respondents="250", u_professors="60", u_staff="45")
    _, joined = _contract_texts(doc.render_hwp(q, tmp_path / "c.hwp", None, extra=extra))
    assert "재학생 250명 이상 교수 60명 이상, 직원 45명 이상" in joined


def test_contract_period_override_recomputes_months(tmp_path):
    doc = DOCUMENT_TYPES["contract"]
    q = build_quote("한성대학교", "U_P", date(2026, 7, 23))
    extra = _contract_extra(period_start="2026-09-01", period_end="2027-02-28")
    _, joined = _contract_texts(doc.render_hwp(q, tmp_path / "c.hwp", None, extra=extra))
    assert "2026년 9월 1일부터 2027년 2월 28일까지(만 6개월)" in joined
    assert "2026. 9. 1. ∼ 2027. 2. 28." in joined


@pytest.mark.parametrize("code", [
    "K_P", "U_P", "K_P_1", "K_P_12", "U_P_1",
    "K_P_1+U_P", "K_P_12+U_P_1", "K_P_12+U_B", "K_B+U_P",
])
def test_contract_all_valid_combos_integrity(tmp_path, code):
    """유효한 모든 조합이 손상 없는 HWP(재직렬화 일치·마커 균형·범위태그 정합)."""
    doc = DOCUMENT_TYPES["contract"]
    q = build_quote("한성대학교", code, date(2026, 7, 23))
    out = doc.render_hwp(q, tmp_path / "c.hwp", None, extra=_contract_extra())
    sm = {tuple(p): d for p, d in cfbf.read_streams(str(out))}
    from quote_automation.hwp_writer import serialize_records
    data = zlib.decompress(sm[("BodyText", "Section0")], -15)
    recs = parse_records(data)
    assert serialize_records(recs) == data       # 재직렬화 일치 (손상 아님)
    _assert_contract_structure_ok(recs)
    # 형광펜(하이라이트)은 원본 계약서에 없음 → 산출물에도 없어야 한다
    assert not any(r.tag == 70 for r in recs)


def test_contract_keeps_memos_except_survey_notes_and_title(tmp_path):
    """계약서는 인쇄되지 않는 실무 안내 메모(9개)는 그대로 두고,
    ① 제목에 붙은 프로세스 체크리스트 메모(1개)와
    ② 특이사항에 걸려있던 인라인 메모 2개(재학생용·UICA용)만 제거한다.
    원본 12개 → 9개. 메모 내용은 본문 텍스트로 새어나오지 않는다."""
    doc = DOCUMENT_TYPES["contract"]
    q = build_quote("한성대학교", "K_P_1+U_P", date(2026, 7, 23))
    out = doc.render_hwp(q, tmp_path / "c.hwp", None, extra=_contract_extra())
    recs, joined = _contract_texts(out)
    assert sum(1 for r in recs if r.tag == 71 and r.payload[:4] == b"knu%") == 9
    assert sum(1 for r in recs if r.tag == 93) == 9
    assert "계약담당 연구원" not in joined and "프로세스" not in joined
    # 제목은 메모 없이 순수 텍스트로 보존되어야 한다
    assert "자문 계약서" in joined
    # 특이사항 문단에는 메모가 남지 않아야 한다(순수 텍스트로 다시 씀)
    for r in recs:
        if r.tag == 67 and "분석 결과의 타당성" in text_of(r):
            u = struct.unpack(f"<{len(r.payload) // 2}H", r.payload)
            assert not any(u[k] in (3, 4) and k + 1 < len(u) and u[k + 1] == 0x6D65
                           for k in range(len(u)))
    _assert_contract_structure_ok(recs)


def test_generate_contract_hwp_only_even_if_pdf_requested(tmp_path):
    files = generate("한성대학교", "K_P_1+U_P", tmp_path, date(2026, 7, 23),
                     formats=["hwp", "pdf"], doc_types=["contract"], extra=_contract_extra())
    assert [f.path.suffix for f in files] == [".hwp"]
    assert files[0].path.is_file()


# --------------------------------------------------------------------------- #
# 독점 공급 확인서 (HWP + PDF, 도구 구성에 따라 본문이 통째로 바뀜)
# --------------------------------------------------------------------------- #
def _exsupply_texts(out_path):
    sm = {tuple(p): d for p, d in cfbf.read_streams(str(out_path))}
    recs = parse_records(zlib.decompress(sm[("BodyText", "Section0")], -15))
    return recs, "\n".join(text_of(r) for r in recs if r.tag == 67)


def _norm(s: str) -> str:
    """연속 공백을 한 칸으로 뭉갠다.

    본문 편집이 메모 필드 마커의 절대 위치를 지키기 위해 지운 자리를
    공백으로 채우므로(같은 글자 수 유지 — 문서 보안설정 [높음] 대응,
    exclusive_supply_writer 모듈 docstring 참고), 실제 생성된 텍스트에는
    참고 문서에 없는 공백이 섞여 있다. 문구 자체가 맞는지만 확인할 때는
    공백을 정규화하고 비교한다."""
    return " ".join(s.split())


def _assert_exsupply_structure_ok(recs):
    """메모 정합성(인라인 id == CTRL_HEADER id == MEMO_LIST id), 필드 마커
    균형, 글자모양 위치·개수 정합을 확인한다(계약서에서 겪은 손상 패턴과 동일)."""
    inline_ids: List[int] = []
    begin = end = 0
    for r in recs:
        if r.tag != 67:
            continue
        u = struct.unpack(f"<{len(r.payload) // 2}H", r.payload)
        begin += sum(1 for c in u if c == 3)
        end += sum(1 for c in u if c == 4)
        k = 0
        while k < len(u):
            if u[k] == 4 and k + 5 < len(u) and u[k + 1] == 0x6D65:
                inline_ids.append(u[k + 5])
                k += 8
            elif u[k] in {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23}:
                k += 8
            else:
                k += 1
    ctrl_ids = [struct.unpack("<I", r.payload[-4:])[0] for r in recs
                if r.tag == 71 and r.payload[:4] == b"knu%"]
    memo_ids = [struct.unpack("<I", r.payload[0:4])[0] for r in recs if r.tag == 93]
    assert sorted(set(inline_ids)) == sorted(ctrl_ids) == sorted(memo_ids), \
        f"메모 정합 깨짐 inline={sorted(set(inline_ids))} ctrl={sorted(ctrl_ids)} memo={sorted(memo_ids)}"
    assert begin == end, f"필드 마커 불균형(begin={begin}, end={end}) = 파일 손상"

    # 문서 보안설정 [높음] 대응 회귀 방지: 이 서류의 메모 앵커(CTRL_HEADER)
    # 6개는 어떤 도구 조합이든 절대 지워지면 안 된다(지우면 높음 보안에서
    # 파일이 열리지 않음 — 실측 확인). MEMO_LIST(메모 내용) 안 문단도 완전히
    # 빈 문자열이면 그 자체로 파일이 손상되므로, 항상 글자가 1개 이상 있어야
    # 한다(공백 1개도 인정 — "내용을 안 보이게" 비우는 유일한 안전한 방법).
    assert sorted(ctrl_ids) == [1, 2, 3, 4, 5, 6], f"메모 앵커가 지워짐: {sorted(ctrl_ids)}"
    for i, r in enumerate(recs):
        if r.tag != 93:
            continue
        for j in range(i + 1, len(recs)):
            if recs[j].tag == 93:
                break
            if recs[j].tag == 67:
                assert len(recs[j].payload) > 0, "MEMO_LIST 문단이 완전히 비어있음 → 파일 손상 위험"

    for i, r in enumerate(recs):
        if r.tag != 66:
            continue
        txt = cs = None
        for j in range(i + 1, min(i + 8, len(recs))):
            if recs[j].tag == 67 and txt is None:
                txt = recs[j]
            if recs[j].tag == 68 and cs is None:
                cs = recs[j]
            if recs[j].tag == 66:
                break
        tlen = len(txt.payload) // 2 if txt else 0
        declared = struct.unpack("<H", r.payload[12:14])[0]
        if cs is not None and tlen > 0:
            n = len(cs.payload) // 8
            assert n == declared, f"글자모양 개수 불일치 para {i}"
            for k in range(n):
                pos = struct.unpack_from("<II", cs.payload, k * 8)[0]
                assert pos < tlen, f"글자모양 위치 범위초과 para {i}: {pos}>={tlen}"

    # 회귀 방지: 긴 문단(예: 본문, 600자 안팎)을 다시 쓰면서 줄나눔(LINE_SEG)을
    # "1줄"로 선언해버리면 한글이 모든 글자를 한 줄에 욱여넣어 겹쳐 그린다
    # (실제로 겪은 손상). 100자를 넘는 문단은 반드시 2줄 이상으로 선언돼 있어야
    # 한다 — LINE_SEG 를 아예 손대지 않는 한(권장 방식) 항상 만족된다.
    for i, r in enumerate(recs):
        if r.tag != 67:
            continue
        tlen = len(r.payload) // 2
        if tlen <= 100:
            continue
        for j in range(i + 1, min(i + 4, len(recs))):
            if recs[j].tag == 69:
                segs = len(recs[j].payload) // 36
                assert segs > 1, f"긴 문단(len={tlen})이 1줄로 선언됨 para {i} → 글자 겹침 위험"
                break


def test_exclusive_supply_registered():
    assert "exclusive_supply" in DOCUMENT_TYPES
    doc = DOCUMENT_TYPES["exclusive_supply"]
    assert doc.label == "독점공급확인서"
    assert doc.supports_pdf is True
    assert template_path(doc).exists()


def _memo_ctrl_ids(recs):
    return sorted(struct.unpack("<I", r.payload[-4:])[0] for r in recs
                  if r.tag == 71 and r.payload[:4] == b"knu%")


def _memo_note_texts(recs):
    """MEMO_LIST(메모 내용) 안 문단들의 텍스트 목록."""
    out = []
    for i, r in enumerate(recs):
        if r.tag != 93:
            continue
        for j in range(i + 1, len(recs)):
            if recs[j].tag == 93:
                break
            if recs[j].tag == 67:
                out.append(text_of(recs[j]))
    return out


def test_exclusive_supply_k_only_matches_reference(tmp_path):
    """배재대학교 참고본과 같은 취지의 K-NSSE 단독 문구가 되어야 한다.

    메모 앵커(CTRL_HEADER)는 문서 보안설정 [높음] 대응을 위해 절대 지우지
    않으므로(exclusive_supply_writer 모듈 docstring 참고), 메모가 감싸던
    UICA 구간 자리에는 원문과 같은 길이의 공백이 남는다 — 정확히 같은
    문구는 아니지만(공백 정규화 후 비교), 뜻은 참고본과 같다."""
    doc = DOCUMENT_TYPES["exclusive_supply"]
    q = build_quote("배재대학교", "K_P_12", date(2025, 8, 28))
    out = doc.render_hwp(q, tmp_path / "e.hwp", None)
    recs, joined = _exsupply_texts(out)
    norm = _norm(joined)
    assert "자문계약명 : 학부교육의 질과 성과 진단 및 분석" in norm
    assert "학부교육 실태조사(K-NSSE)와 를 진단도구로 사용하고 있습니다" in norm
    assert "이에 배재대학교의 학부교육의 질과 성과, 등에 대한" in norm
    assert "대학 혁신역량 진단조사(UICA)" not in joined
    assert "혁신 역량" not in joined
    assert "2025. 8. 28." in joined
    assert "OO대학교" not in joined
    # 메모 앵커는 6개 그대로(절대 건드리지 않음), 내용만 공백으로 비워짐.
    assert _memo_ctrl_ids(recs) == [1, 2, 3, 4, 5, 6]
    assert sum(1 for r in recs if r.tag == 93) == 6
    assert all(t.strip() == "" and t != "" for t in _memo_note_texts(recs))
    _assert_exsupply_structure_ok(recs)


def test_exclusive_supply_u_only_matches_reference(tmp_path):
    """포항공과대학교 참고본과 같은 취지의 UICA 단독 문구가 되어야 한다.

    UICA 관련 메모 구간(본문 안 5곳)은 전혀 건드리지 않으므로 이 경우는
    참고본과 완전히 같은 문구가 나온다(제목 인용구만 길이를 맞추려고
    끝에 공백 1개가 남는다)."""
    doc = DOCUMENT_TYPES["exclusive_supply"]
    q = build_quote("포항공과대학교", "U_P_1", date(2025, 9, 3))
    out = doc.render_hwp(q, tmp_path / "e.hwp", None)
    recs, joined = _exsupply_texts(out)
    norm = _norm(joined)
    assert "자문계약명 : 대학 혁신역량 진단 및 분석" in norm
    assert "학부교육의 질과 성과" not in joined
    assert "학부교육 실태조사(K-NSSE)" not in joined
    assert "대학 혁신역량 진단조사(UICA)를 진단도구로 사용하고 있습니다" in norm
    assert "이에 포항공과대학교의 혁신 역량 등에 대한 자문을" in norm
    assert "2025. 9. 3." in joined
    # 메모 앵커/내용 어느 쪽도 건드리지 않음(K-NSSE 단독과 달리 5곳 다 원본 그대로).
    assert _memo_ctrl_ids(recs) == [1, 2, 3, 4, 5, 6]
    assert sum(1 for r in recs if r.tag == 93) == 6
    _assert_exsupply_structure_ok(recs)


def test_exclusive_supply_combined_blanks_memo_notes_only(tmp_path):
    """K+U 결합이면 본문(눈에 보이는 글자)은 손대지 않고 대학명만 채운다.

    메모는 대학에 보일 필요가 없으므로(사용자 확정 규칙) "내용"만 안 보이게
    비우되, 앵커(CTRL_HEADER)는 절대 건드리지 않는다 — 앵커를 지우면 문서
    보안설정 [높음]에서 파일이 열리지 않기 때문(실측 확인)."""
    doc = DOCUMENT_TYPES["exclusive_supply"]
    q = build_quote("한성대학교", "K_P_12+U_P_1", date(2025, 9, 10))
    out = doc.render_hwp(q, tmp_path / "e.hwp", None)
    recs, joined = _exsupply_texts(out)
    assert "자문계약명 : 학부교육의 질과 성과 진단 및 분석" in joined
    assert "학부교육 실태조사(K-NSSE)와 대학 혁신역량 진단조사(UICA)를 진단도구로" in joined
    assert "한성대학교의 학부교육의 질과 성과, 혁신 역량 등에 대한" in joined
    assert "OO대학교" not in joined
    assert _memo_ctrl_ids(recs) == [1, 2, 3, 4, 5, 6]
    assert sum(1 for r in recs if r.tag == 93) == 6
    assert all(t.strip() == "" and t != "" for t in _memo_note_texts(recs))
    _assert_exsupply_structure_ok(recs)


@pytest.mark.parametrize("code,univ", [
    ("K_B", "가나다대학교"), ("K_P", "가나다대학교"), ("K_P_12", "가나다대학교"),
    ("U_B", "가나다대학교"), ("U_P", "가나다대학교"), ("U_P_1", "가나다대학교"),
    ("K_B+U_B", "가나다대학교"), ("K_P_12+U_P_1", "가나다대학교"), ("K_B+U_P_1", "가나다대학교"),
])
def test_exclusive_supply_all_combos_integrity(tmp_path, code, univ):
    """등급 무관(베이직 포함) 모든 조합에서 손상 없는 HWP 를 만든다."""
    doc = DOCUMENT_TYPES["exclusive_supply"]
    q = build_quote(univ, code, date(2025, 8, 28))
    out = doc.render_hwp(q, tmp_path / "e.hwp", None)
    sm = {tuple(p): d for p, d in cfbf.read_streams(str(out))}
    data = zlib.decompress(sm[("BodyText", "Section0")], -15)
    recs = parse_records(data)
    from quote_automation.hwp_writer import serialize_records
    assert serialize_records(recs) == data
    _assert_exsupply_structure_ok(recs)


def test_exclusive_supply_pdf_renders(tmp_path):
    pytest.importorskip("pypdfium2")
    import pypdfium2 as pdfium
    doc = DOCUMENT_TYPES["exclusive_supply"]
    q = build_quote("배재대학교", "K_P_12", date(2025, 8, 28))
    out = doc.render_pdf(q, tmp_path / "e.pdf")
    text = pdfium.PdfDocument(str(out))[0].get_textpage().get_text_range()
    assert "독점 공급 확인서" in text
    assert "배재대학교" in text
    assert "2025. 8. 28." in text


def test_generate_exclusive_supply_hwp_and_pdf(tmp_path):
    files = generate("배재대학교", "K_P_12", tmp_path, date(2025, 8, 28),
                     formats=["hwp", "pdf"], doc_types=["exclusive_supply"])
    assert sorted(f.path.suffix for f in files) == [".hwp", ".pdf"]
    assert all(f.path.is_file() for f in files)
