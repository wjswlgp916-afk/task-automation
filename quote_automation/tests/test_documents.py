"""문서 종류(거래명세서 등) 등록·생성 검증 테스트."""

import sys
import zlib
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quote_automation.documents import DOCUMENT_TYPES, template_path  # noqa: E402
from quote_automation.engine import build_quote  # noqa: E402
from quote_automation.generator import generate, GeneratedFile  # noqa: E402
from quote_automation.hwp_writer import render_hwp, parse_records, text_of  # noqa: E402
from quote_automation import cfbf  # noqa: E402

ALL_CODES = [
    "K_B", "K_P", "K_P_1", "K_P_2", "K_P_12",
    "U_B", "U_P", "U_P_1",
    "K_P_12+U_P_1", "K_P_2+U_B",
]


def test_transaction_statement_template_registered():
    assert "transaction_statement" in DOCUMENT_TYPES
    doc = DOCUMENT_TYPES["transaction_statement"]
    assert doc.label == "거래명세서"
    assert doc.supports_pdf is False
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
    assert by_label["거래명세서"] == [".hwp"]   # PDF 미지원이므로 HWP만
    for gf in files:
        assert gf.path.is_file()


def test_generate_unknown_doc_type_raises(tmp_path):
    with pytest.raises(ValueError):
        generate("테스트대학교", "U_P", tmp_path, date(2026, 7, 21),
                 doc_types=["존재하지않는서류"])


def test_quote_template_still_default_when_doctype_not_specified(tmp_path):
    files = generate("테스트대학교", "U_P", tmp_path, date(2026, 7, 21))
    assert len(files) == 2  # 기본값 quote: HWP + PDF
    assert all(gf.doc_label == "견적서" for gf in files)
