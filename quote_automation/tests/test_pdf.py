"""PDF 렌더링 검증 테스트.

reportlab 이 만든 페이지를 pypdfium2 로 다시 읽어 텍스트/이미지가
의도대로 반영됐는지 확인한다 (합계 통합 표시, 부가서비스 들여쓰기, 도장 삽입).
"""

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytest.importorskip("pypdfium2")
import pypdfium2 as pdfium  # noqa: E402

from quote_automation.engine import build_quote  # noqa: E402
from quote_automation.pdf_writer import render_pdf, _STAMP_PATH  # noqa: E402


def _text(path) -> str:
    pdf = pdfium.PdfDocument(str(path))
    return pdf[0].get_textpage().get_text_range()


def test_total_row_shows_single_combined_amount(tmp_path):
    q = build_quote("호서대학교", "K_P_12+U_B", date(2026, 7, 21))
    out = render_pdf(q, tmp_path / "q.pdf")
    text = _text(out)
    assert "합 계" in text
    assert "4,400,000" in text
    # 합계 행에는 공급가액(4,000,000)·세액(400,000)이 따로 나오면 안 되고
    # 부가세 포함 합계 한 값(4,400,000)만 토큰으로 나와야 한다.
    tail_tokens = text.split("합 계")[-1].split()
    assert "4,000,000" not in tail_tokens
    assert "400,000" not in tail_tokens
    assert "4,400,000" in tail_tokens


def test_addon_rows_are_visually_indented(tmp_path):
    q = build_quote("테스트대학교", "K_P_12", date(2026, 7, 21))
    out = render_pdf(q, tmp_path / "q.pdf")
    text = _text(out)
    assert "└ 단과대학별 분석 및 보고서 제공" in text
    assert "└ Peer Benchmarking" in text
    # 본품 이름 앞에는 들여쓰기 기호가 없어야 한다
    assert "└ 학부교육의 질과 성과" not in text


def test_header_lines_present(tmp_path):
    q = build_quote("호서대학교", "U_P", date(2026, 7, 21))
    out = render_pdf(q, tmp_path / "q.pdf")
    text = _text(out)
    assert "서기 2026년 07월 21일" in text
    assert "호서대학교  귀중" in text or "호서대학교 귀중" in text
    assert "아래와 같이 견적합니다." in text


def test_stamp_asset_exists_and_is_used(tmp_path):
    assert _STAMP_PATH.exists(), "도장 이미지 자산(templates/stamp.png)이 없습니다."
    q = build_quote("테스트대학교", "U_B", date(2026, 7, 21))
    out = render_pdf(q, tmp_path / "q.pdf")
    pdf = pdfium.PdfDocument(str(out))
    page = pdf[0]
    # 대표자 셀에 삽입한 도장 이미지가 실제 페이지 오브젝트로 존재하는지 확인
    kinds = [obj.type for obj in page.get_objects()]
    assert pdfium.raw.FPDF_PAGEOBJ_IMAGE in kinds, "도장 이미지가 PDF에 삽입되지 않았습니다."
