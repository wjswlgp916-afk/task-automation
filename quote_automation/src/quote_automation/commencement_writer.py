"""착수계 렌더러.

바뀌는 항목:
  1. 용 역 명   : 견적서/대금청구서/각서/검수확인서/완료계와 동일 규칙
                  (K 단독·결합 유지, U 단독 변경)
  2. 계약금액   : 견적 합계와 자동 연동 -> "금  OOO만 원(￦ O,OOO,OOO )"
                  (완료계와 같은 만원 단위 숫자 표기, "금" 뒤 공백 2칸만 다름)
  3. 계약년월일 : 대학마다 달라 매번 입력해야 함 (사용자 입력, extra, 필수).
                  완료계와 같은 계약이므로 extra 키를 ``contract_date`` 로
                  공유한다 — 완료계와 함께 만들 때 한 번만 입력하면 된다.
  4. 완료기한   : 보통 고정, 기본값 있음(2027-01-31). 완료계와 같은 extra 키
                  ``completion_deadline`` 을 공유한다.
  서류 발급일자: 그 서류를 발급한 날(quote.issue_date) — 착수를 알리는 날짜.

원본 양식에는 착수년월일이 따로 없다(이 서류 자체가 착수를 알리는 문서라
발급일자가 곧 착수 시점). 상호·소재지·대표자는 원본 양식에 이미 고정
텍스트로 박혀 있어(성균관대학교 산학협력단 정보와 일치) 따로 채울 필요가
없다.
"""

from __future__ import annotations

import struct
import zlib
from datetime import date as date_cls
from pathlib import Path
from typing import Optional

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

from . import cfbf, pdf_common
from .catalog import COMPANY
from .engine import Quote, subject_override
from .hwp_writer import (
    parse_records, serialize_records, replace_literal_everywhere, strip_memo_controls,
    strip_highlight_ranges,
)

# 원본 양식의 정확한 placeholder 리터럴
_SUBJECT = "학부교육의 질과 성과 진단 및 분석"
_AMOUNT_PLACEHOLDER = "금  OOOO만 원(￦ 00,000 )"
_CONTRACT_PLACEHOLDER = "2026년   9월   1일"
_DEADLINE_PLACEHOLDER = "2027년   1월   31일"
_ISSUE_PLACEHOLDER = "2026년   0월  0일"
_UNIV_PLACEHOLDER = "OO대학교"

DEFAULT_DEADLINE = date_cls(2027, 1, 31)


class CommencementError(RuntimeError):
    pass


def _default_template() -> Path:
    return Path(__file__).parent / "templates" / "commencement_template.hwp"


def _fmt_body(d: date_cls) -> str:
    return f"{d.year}년   {d.month:02d}월   {d.day:02d}일"


def _fmt_issue(d: date_cls) -> str:
    return f"{d.year}년   {d.month:02d}월  {d.day:02d}일"


def _man_amount_text(amount: int) -> str:
    return f"금  {amount // 10000}만 원(￦ {amount:,} )"


def _dates(extra: Optional[dict]):
    extra = extra or {}
    contract_date = extra.get("contract_date")
    completion_deadline = extra.get("completion_deadline") or DEFAULT_DEADLINE
    if not contract_date:
        raise CommencementError("착수계에는 계약년월일이 필요합니다.")
    return contract_date, completion_deadline


def render_hwp(quote: Quote, out_path: str | Path, template: Optional[Path] = None,
               extra: Optional[dict] = None) -> Path:
    """착수계를 HWP 파일로 저장하고 경로를 반환한다."""
    contract_date, completion_deadline = _dates(extra)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tpl = Path(template) if template else _default_template()

    streams = cfbf.read_streams(str(tpl))
    sm = {tuple(p): d for p, d in streams}
    (flags,) = struct.unpack("<I", sm[("FileHeader",)][36:40])
    compressed = bool(flags & 1)
    raw = sm[("BodyText", "Section0")]
    data = zlib.decompress(raw, -15) if compressed else raw
    records = parse_records(data)
    strip_memo_controls(records)
    strip_highlight_ranges(records)

    amount = quote.grand_total

    def rep(old, new, what):
        if replace_literal_everywhere(records, old, new) != 1:
            raise CommencementError(f"템플릿에서 '{what}' 자리를 찾지 못했습니다.")

    rep(_AMOUNT_PLACEHOLDER, _man_amount_text(amount), "계약금액")
    rep(_CONTRACT_PLACEHOLDER, _fmt_body(contract_date), "계약년월일")
    rep(_DEADLINE_PLACEHOLDER, _fmt_body(completion_deadline), "완료기한")
    rep(_ISSUE_PLACEHOLDER, _fmt_issue(quote.issue_date), "발급일자")
    rep(_UNIV_PLACEHOLDER, quote.university, "대학명")

    subject = subject_override(quote)
    if subject:
        rep(_SUBJECT, subject, "용역명")

    body = serialize_records(records)
    if compressed:
        co = zlib.compressobj(9, zlib.DEFLATED, -15)
        packed = co.compress(body) + co.flush()
    else:
        packed = body

    new_streams = [
        (parts, packed if tuple(parts) == ("BodyText", "Section0") else d)
        for parts, d in streams
    ]
    cfbf.write_cfbf(str(out_path), new_streams)
    return out_path


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
def render_pdf(quote: Quote, out_path: str | Path, extra: Optional[dict] = None) -> Path:
    """착수계를 PDF 파일로 저장하고 경로를 반환한다."""
    contract_date, completion_deadline = _dates(extra)
    bold = pdf_common.register_fonts()
    font = pdf_common.FONT
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    st = {
        "title": ParagraphStyle("title", fontName=bold, fontSize=22, alignment=TA_CENTER, leading=28),
        "item": ParagraphStyle("item", fontName=font, fontSize=12.5, alignment=TA_LEFT, leading=22),
        "center": ParagraphStyle("center", fontName=font, fontSize=12.5, alignment=TA_CENTER, leading=18),
        "iss_label": ParagraphStyle("iss_label", fontName=font, fontSize=11.5, alignment=TA_LEFT, leading=16),
        "iss_value": ParagraphStyle("iss_value", fontName=font, fontSize=11.5, alignment=TA_LEFT, leading=16),
        "recipient": ParagraphStyle("recipient", fontName=bold, fontSize=13, alignment=TA_LEFT, leading=18),
    }

    left = right = 24 * mm
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=left, rightMargin=right, topMargin=22 * mm, bottomMargin=20 * mm,
        title=f"착수계_{quote.university}",
    )

    subject = subject_override(quote) or _SUBJECT
    amount = quote.grand_total

    story = []
    story.append(Paragraph("착 수 계", st["title"]))
    story.append(Spacer(1, 12 * mm))

    story.append(Paragraph(f"1. 용  역  명 : {subject}", st["item"]))
    story.append(Paragraph(f"2. 계약금액 : {_man_amount_text(amount)}", st["item"]))
    story.append(Paragraph(f"3. 계약년월일 : {_fmt_body(contract_date)}", st["item"]))
    story.append(Paragraph(f"4. 완료기한 : {_fmt_body(completion_deadline)}", st["item"]))
    story.append(Spacer(1, 8 * mm))

    story.append(Paragraph("위와 같이 용역을 착수하였기에 착수계를 제출합니다.", st["center"]))
    story.append(Spacer(1, 12 * mm))

    story.append(Paragraph(_fmt_issue(quote.issue_date), st["center"]))
    story.append(Spacer(1, 16 * mm))

    _NBSP = " "
    ceo_value = "구 자 춘" + _NBSP * 10 + "(인)"
    issuer = Table(
        [
            [Paragraph("제출자", st["iss_label"]), Paragraph("상    호:", st["iss_label"]),
             Paragraph(COMPANY.name, st["iss_value"])],
            ["", Paragraph("주    소:", st["iss_label"]), Paragraph(COMPANY.address, st["iss_value"])],
            ["", Paragraph("대 표 자:", st["iss_label"]), Paragraph(ceo_value, st["iss_value"])],
        ],
        colWidths=[18 * mm, 20 * mm, 124 * mm], rowHeights=8.6 * mm,
    )
    issuer.hAlign = "LEFT"
    issuer.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(issuer)

    if pdf_common.STAMP_PATH.exists():
        brace_at = ceo_value.find("(")
        prefix, brace = ceo_value[:brace_at], ceo_value[brace_at:]
        value_start = 18 * mm + 20 * mm + 2 * mm
        size = 18 * mm
        x = pdf_common.stamp_x_over(prefix, brace, value_start, font, 11.5, size)
        story.append(pdf_common.StampOverlay(str(pdf_common.STAMP_PATH), size=size,
                                             x=x, overlap=13 * mm))

    story.append(Spacer(1, 16 * mm))
    story.append(Paragraph(f"{quote.university} 귀하", st["recipient"]))

    doc.build(story)
    return out_path
