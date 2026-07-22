"""계약보증금 지급각서 렌더러.

바뀌는 항목:
  1. 계약명   : 견적서/대금청구서와 동일 규칙 (K 단독·결합 유지, U 단독 변경)
  2. 계약금액 : 견적 합계와 자동 연동  ->  "금 …원 정(￦ … ≠)"
  3. 계약보증금: 계약금액의 10% 자동 계산
  4. 계약일   : 계약 시작일 ∼ 종료일 (착수일: …)  ← 사용자 입력(extra)
  서류 발급일자: 그 서류를 발급한 날(quote.issue_date)

extra 딕셔너리로 아래 날짜를 받는다 (datetime.date):
  contract_start   계약 시작일 (필수)
  contract_end     계약 종료일 (보통 고정, 기본값 있음)
  commencement     착수일     (보통 고정, 기본값 있음)
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
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

from . import cfbf, pdf_common
from .catalog import COMPANY
from .engine import Quote, subject_override
from .hwp_writer import (
    parse_records, serialize_records, replace_literal_everywhere, strip_memo_controls,
    strip_highlight_ranges,
)
from .korean_num import number_to_korean_plain

# 특수문자
_WON = "￦"      # ￦
_NEQ = "≠"      # ≠
_TILDE = "∼"    # ∼

# 원본 양식의 정확한 placeholder 리터럴
_SUBJECT = "학부교육의 질과 성과 진단 및 분석"
_AMOUNT = f"금 OO만원 정({_WON} 0,000,000 {_NEQ})"
_DEPOSIT = f"금 OO만원 정({_WON} 000,000 {_NEQ})"
_DATE_LINE = f"2026. 09. 00 {_TILDE} 2027. 01. 31 (착수일: 2026년 9월 1일)"
_ISSUE = "2026년  9월  1일"       # 발급일자 (겹공백 주의)
_UNIV = "OO대학교"

DEPOSIT_RATE = 0.10


class GuarantyError(RuntimeError):
    pass


def _default_template() -> Path:
    return Path(__file__).parent / "templates" / "guaranty_template.hwp"


def _fmt_dot(d: date_cls) -> str:
    return f"{d.year}. {d.month:02d}. {d.day:02d}"


def _fmt_kor(d: date_cls) -> str:
    return f"{d.year}년 {d.month}월 {d.day}일"


def _fmt_issue(d: date_cls) -> str:
    return f"{d.year}년  {d.month}월  {d.day}일"


def _deposit(quote: Quote) -> int:
    return round(quote.grand_total * DEPOSIT_RATE)


def _amount_text(amount: int) -> str:
    return f"금 {number_to_korean_plain(amount)}원 정({_WON} {amount:,} {_NEQ})"


def _dates(extra: Optional[dict]):
    extra = extra or {}
    start = extra.get("contract_start")
    end = extra.get("contract_end")
    comm = extra.get("commencement")
    if not (start and end and comm):
        raise GuarantyError(
            "계약보증금 지급각서에는 계약 시작일·종료일·착수일이 필요합니다."
        )
    return start, end, comm


def render_hwp(quote: Quote, out_path: str | Path, template: Optional[Path] = None,
               extra: Optional[dict] = None) -> Path:
    """계약보증금 지급각서를 HWP 파일로 저장하고 경로를 반환한다."""
    start, end, comm = _dates(extra)
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
    deposit = _deposit(quote)

    def rep(old, new, what):
        if replace_literal_everywhere(records, old, new) != 1:
            raise GuarantyError(f"템플릿에서 '{what}' 자리를 찾지 못했습니다.")

    rep(_AMOUNT, _amount_text(amount), "계약금액")
    rep(_DEPOSIT, _amount_text(deposit), "계약보증금")
    rep(_DATE_LINE, f"{_fmt_dot(start)} {_TILDE} {_fmt_dot(end)} (착수일: {_fmt_kor(comm)})", "계약일")
    rep(_ISSUE, _fmt_issue(quote.issue_date), "발급일자")
    rep(_UNIV, quote.university, "대학명")

    subject = subject_override(quote)
    if subject:
        rep(_SUBJECT, subject, "계약명")

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
_LEGAL_TEXT = (
    "국가계약법 시행령 제50조 제6항 및 지방계약법 시행령 제53조 제1항 제2호의 "
    "규정에 의거 계약보증금을 면제함에 있어, 국가계약법 법률 제12조 제3항 및 "
    "지방계약법 제15조 3항에 따라 계약보증금의 귀속사유가 발생되었을 시 "
    "계약보증금 해당액을 현금으로 납입할 것을 확약하며 이 각서를 제출합니다."
)


def render_pdf(quote: Quote, out_path: str | Path, extra: Optional[dict] = None) -> Path:
    """계약보증금 지급각서를 PDF 파일로 저장하고 경로를 반환한다."""
    start, end, comm = _dates(extra)
    bold = pdf_common.register_fonts()
    font = pdf_common.FONT
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    st = {
        "title": ParagraphStyle("title", fontName=bold, fontSize=20, alignment=TA_CENTER, leading=26),
        "item": ParagraphStyle("item", fontName=font, fontSize=12.5, alignment=TA_LEFT, leading=22),
        "sub": ParagraphStyle("sub", fontName=font, fontSize=11, alignment=TA_LEFT, leading=16),
        "legal": ParagraphStyle("legal", fontName=font, fontSize=12, alignment=TA_JUSTIFY, leading=22),
        "center": ParagraphStyle("center", fontName=font, fontSize=12.5, alignment=TA_CENTER, leading=18),
        "iss_label": ParagraphStyle("iss_label", fontName=font, fontSize=11.5, alignment=TA_LEFT, leading=16),
        "iss_value": ParagraphStyle("iss_value", fontName=font, fontSize=11.5, alignment=TA_LEFT, leading=16),
        "recipient": ParagraphStyle("recipient", fontName=bold, fontSize=13, alignment=TA_LEFT, leading=18),
    }

    left = right = 24 * mm
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=left, rightMargin=right, topMargin=22 * mm, bottomMargin=20 * mm,
        title=f"계약보증금지급각서_{quote.university}",
    )

    subject = subject_override(quote) or _SUBJECT
    amount = quote.grand_total
    deposit = _deposit(quote)

    story = []
    story.append(Paragraph("계약보증금 지급각서", st["title"]))
    story.append(Spacer(1, 12 * mm))

    story.append(Paragraph(f"1. 계  약  명 : {subject}", st["item"]))
    story.append(Paragraph(f"2. 계 약 금 액 : {_amount_text(amount)}", st["item"]))
    story.append(Paragraph(f"3. 계약보증금 : {_amount_text(deposit)}", st["item"]))
    story.append(Paragraph("계약금액의 10%", ParagraphStyle(
        "pct", parent=st["sub"], leftIndent=30 * mm)))
    story.append(Paragraph(
        f"4. 계  약  일 : {_fmt_dot(start)} {_TILDE} {_fmt_dot(end)} "
        f"(착수일: {_fmt_kor(comm)})", st["item"]))
    story.append(Spacer(1, 8 * mm))

    story.append(Paragraph(_LEGAL_TEXT, st["legal"]))
    story.append(Spacer(1, 12 * mm))

    story.append(Paragraph(_fmt_issue(quote.issue_date), st["center"]))
    story.append(Spacer(1, 16 * mm))

    # 제출자 블록
    # reportlab Paragraph 는 일반 공백을 한 칸으로 합치므로, 원본처럼 넓은
    # 간격을 유지하려면 줄바꿈 안 되는 공백(nbsp)을 쓴다. 그래야 stringWidth
    # 로 계산한 도장 위치도 실제 렌더링과 일치한다.
    _NBSP = " "
    ceo_value = "구 자 춘" + _NBSP * 10 + "(인)"
    issuer = Table(
        [
            [Paragraph("제출자", st["iss_label"]), Paragraph("상호명 :", st["iss_label"]),
             Paragraph(COMPANY.name, st["iss_value"])],
            ["", Paragraph("소재지 :", st["iss_label"]), Paragraph(COMPANY.address, st["iss_value"])],
            ["", Paragraph("대표자 :", st["iss_label"]), Paragraph(ceo_value, st["iss_value"])],
        ],
        colWidths=[18 * mm, 20 * mm, 124 * mm], rowHeights=8.6 * mm,
    )
    issuer.hAlign = "LEFT"
    issuer.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(issuer)

    # 도장을 대표자 값의 '(인)' 위에 겹쳐 찍는다.
    if pdf_common.STAMP_PATH.exists():
        brace_at = ceo_value.find("(")
        prefix, brace = ceo_value[:brace_at], ceo_value[brace_at:]
        value_start = 18 * mm + 20 * mm + 2 * mm     # 제출자col + 라벨col + 패딩
        size = 18 * mm
        x = pdf_common.stamp_x_over(prefix, brace, value_start, font, 11.5, size)
        story.append(pdf_common.StampOverlay(str(pdf_common.STAMP_PATH), size=size,
                                             x=x, overlap=13 * mm))

    story.append(Spacer(1, 16 * mm))
    story.append(Paragraph(f"{quote.university} 총장 귀하", st["recipient"]))

    doc.build(story)
    return out_path
