"""HWP 생성 구조 검증 테스트.

한글(Hangul)로 직접 열 수 없는 CI 환경이므로, 생성된 .hwp 가
    * 정상적인 CFBF 컨테이너인지 (olefile 로 열림)
    * 원본 스트림(도장 이미지·DocInfo 등)을 보존하는지
    * 품목 테이블 불변식(행/열 타일링)을 만족하는지
    * 편집한 텍스트(대학명·합계금액)가 반영됐고 안내문구가 제거됐는지
를 확인한다. render_hwp 내부의 _validate 가 실패하면 예외가 나므로,
생성이 성공한다는 것 자체가 1차 검증이다.
"""

import sys
import zlib
from datetime import date
from pathlib import Path

import olefile
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quote_automation.engine import build_quote  # noqa: E402
from quote_automation.hwp_writer import (  # noqa: E402
    render_hwp,
    parse_records,
    text_of,
    _find_item_table,
    _split_cells,
)
from quote_automation import cfbf  # noqa: E402

TEMPLATE = Path(__file__).resolve().parents[1] / "src/quote_automation/templates/quote_template.hwp"

ALL_CODES = [
    "K_B", "K_P", "K_P_1", "K_P_2", "K_P_12",
    "U_B", "U_P", "U_P_1",
    "K_P_12+U_P_1", "K_P_2+U_B", "K_B+U_B", "U_P_1+K_B",
]


def _table_rows(path):
    sm = {tuple(p): d for p, d in cfbf.read_streams(str(path))}
    recs = parse_records(zlib.decompress(sm[("BodyText", "Section0")], -15))
    ti, end, lvl = _find_item_table(recs)
    cells = _split_cells(recs[ti + 1:end], lvl)
    rows = {}
    for c in cells:
        rows.setdefault(c.row(), []).append(c)
    return recs, rows


@pytest.mark.parametrize("code", ALL_CODES)
def test_generates_valid_container(tmp_path, code):
    q = build_quote("테스트대학교", code, date(2026, 7, 21))
    out = render_hwp(q, tmp_path / "q.hwp")           # 내부 _validate 통과해야 함
    ole = olefile.OleFileIO(str(out))
    assert ole.exists("BodyText/Section0")
    assert ole.exists("DocInfo")
    ole.close()


@pytest.mark.parametrize("code", ALL_CODES)
def test_preserves_original_streams(tmp_path, code):
    tpl = {tuple(p): d for p, d in cfbf.read_streams(str(TEMPLATE))}
    q = build_quote("테스트대학교", code, date(2026, 7, 21))
    out = render_hwp(q, tmp_path / "q.hwp")
    got = {tuple(p): d for p, d in cfbf.read_streams(str(out))}
    # 스트림 집합 동일
    assert set(got) == set(tpl)
    # 편집 대상이 아닌 스트림은 바이트 동일
    for k in [("DocInfo",), ("BinData", "BIN0001.png"), ("FileHeader",)]:
        assert got[k] == tpl[k], f"{k} 변경됨"


def test_item_rows_match_quote(tmp_path):
    q = build_quote("강남대학교", "K_P_2+U_B", date(2025, 8, 19))
    out = render_hwp(q, tmp_path / "q.hwp")
    _, rows = _table_rows(out)
    # 요약 + 헤더 + 데이터 3행 + 합계 = 6 행
    assert len(rows) == 6
    data_texts = [
        [c.first_text() for c in sorted(rows[r], key=lambda c: c.col())]
        for r in sorted(rows)
    ]
    # 데이터 행 3개: K본품, Peer부가, UICA베이직
    joined = "\n".join(" | ".join(t) for t in data_texts)
    assert "K-NSSE" in joined and "PREMIER" in joined
    assert "Peer Benchmarking" in joined
    assert "BASIC" in joined


def test_university_and_amount_applied(tmp_path):
    q = build_quote("한국대학교", "U_P", date(2026, 1, 2))
    out = render_hwp(q, tmp_path / "q.hwp")
    recs, _ = _table_rows(out)
    texts = [text_of(r) for r in recs if r.tag == 67]
    assert any("한국대학교  귀중" in t for t in texts)
    assert any("이백이십만 원정" in t for t in texts)
    assert any("2026년" in t and "01월" in t for t in texts)


def test_template_notes_removed(tmp_path):
    q = build_quote("테스트대", "K_P", date(2026, 7, 21))
    out = render_hwp(q, tmp_path / "q.hwp")
    recs, _ = _table_rows(out)
    texts = " ".join(text_of(r) for r in recs if r.tag == 67)
    for marker in ["일백육십오만", "총 금액 한글로", "베이직 경우", "신청서에 맞게"]:
        assert marker not in texts, f"안내문구 잔존: {marker}"


def test_kbasic_product_name_changed(tmp_path):
    q = build_quote("무료대", "K_B", date(2026, 7, 21))
    out = render_hwp(q, tmp_path / "q.hwp")
    _, rows = _table_rows(out)
    joined = "\n".join(
        " | ".join(c.first_text() for c in sorted(rows[r], key=lambda c: c.col()))
        for r in sorted(rows)
    )
    # 베이직 행의 품명이 K-NSSE 로 바뀌어 있어야 함 (원본은 UICA)
    assert "K-NSSE" in joined and "BASIC" in joined


@pytest.mark.parametrize("code", ALL_CODES)
def test_no_leftover_memo_controls(tmp_path, code):
    """원본 quote_template.hwp 에 남아있던 한글 메모(코멘트)가 출력물에
    남으면 한글에서 '메모를 읽는 중 오류' 경고가 뜨므로, 생성물에는
    메모 컨트롤이 하나도 없어야 한다."""
    q = build_quote("테스트대학교", code, date(2026, 7, 21))
    out = render_hwp(q, tmp_path / "q.hwp")
    recs, _ = _table_rows(out)
    memos = [r for r in recs if r.tag == 71 and r.payload[:4] == b"knu%"]
    assert memos == []


def test_memo_removal_preserves_visible_text(tmp_path):
    """메모를 제거해도 그 문단의 눈에 보이는 텍스트(품명 헤더 등)는 그대로."""
    q = build_quote("테스트대학교", "K_P", date(2026, 7, 21))
    out = render_hwp(q, tmp_path / "q.hwp")
    recs, _ = _table_rows(out)
    texts = [text_of(r) for r in recs if r.tag == 67]
    assert "품       명" in texts
