"""하자(보수)이행보증각서 렌더러.

바뀌는 항목:
  1. 계약명     : 견적서 등과 동일 규칙(K 단독·결합 유지, U 단독 변경)
  2. 계약기간   : 계약서와 같은 자문기간 — extra 키 period_start/period_end 를
                  공유한다(기본 2026-09-01 ~ 2027-01-31).
  3. 계약금     : 견적 합계 -> 한글 금액"이백이십만원정(₩2,200,000)"
  4. 하자보증금 : 계약금의 3%(고정 요율, DEFECT_RATE) -> 같은 한글 금액 표기
  5. 하자보증율 : "계약금액의 3%" (고정)
  6. 하자보증기간: 계약 종료일 다음날부터 1년간(자동 계산, 별도 입력 없음)
  서류 발급일자: 그 서류를 발급한 날(quote.issue_date)

원본 양식에 이미 메모가 없는 버전을 템플릿으로 쓴다(사용자 확인). 상호·
소재지·대표자는 원본 양식에 이미 고정 텍스트로 박혀 있어(성균관대학교
산학협력단 정보와 일치) 따로 채울 필요가 없다.
"""

from __future__ import annotations

import struct
import zlib
from datetime import date as date_cls, timedelta
from pathlib import Path
from typing import Optional, Tuple

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

# 원본 양식의 정확한 placeholder 리터럴
_SUBJECT = "학부교육의 질과 성과 진단 및 분석"
_PERIOD_PLACEHOLDER = "2026.  00.  00. ~ 2027.  01.  31."
_AMOUNT_PLACEHOLDER = "0백0십0만원정(₩0,000,000)"
_DEPOSIT_PLACEHOLDER = "0만0천원정(₩00,000)"
_RATE_PLACEHOLDER = "계약금액의 0%"
_DEFECT_PERIOD_PLACEHOLDER = "2027. 02. 01 ~ 2028. 02. 01"
_ISSUE_PLACEHOLDER = "2026년  00월  00일"
_UNIV_PLACEHOLDER = "00대학교"

DEFAULT_PERIOD_START = date_cls(2026, 9, 1)
DEFAULT_PERIOD_END = date_cls(2027, 1, 31)
DEFECT_RATE = 0.03    # 하자보증율(계약금액의 3%, 고정)


class WarrantyBondError(RuntimeError):
    pass


def _default_template() -> Path:
    return Path(__file__).parent / "templates" / "warranty_bond_template.hwp"


def _fmt_period(start: date_cls, end: date_cls) -> str:
    return (f"{start.year}.  {start.month:02d}.  {start.day:02d}. ~ "
            f"{end.year}.  {end.month:02d}.  {end.day:02d}.")


def _fmt_defect_period(start: date_cls, end: date_cls) -> str:
    return (f"{start.year}. {start.month:02d}. {start.day:02d} ~ "
            f"{end.year}. {end.month:02d}. {end.day:02d}")


def _fmt_issue(d: date_cls) -> str:
    return f"{d.year}년  {d.month:02d}월  {d.day:02d}일"


def _amount_text(amount: int) -> str:
    return f"{number_to_korean_plain(amount)}원정(₩{amount:,})"


def _defect_period(period_end: date_cls) -> Tuple[date_cls, date_cls]:
    """하자보증기간: 계약 종료일 다음날부터 1년간."""
    start = period_end + timedelta(days=1)
    try:
        end = start.replace(year=start.year + 1)
    except ValueError:            # 2/29 시작인 윤년 예외
        end = start.replace(year=start.year + 1, day=28)
    return start, end


def _periods(extra: Optional[dict]):
    extra = extra or {}
    period_start = extra.get("period_start") or DEFAULT_PERIOD_START
    period_end = extra.get("period_end") or DEFAULT_PERIOD_END
    return period_start, period_end


def render_hwp(quote: Quote, out_path: str | Path, template: Optional[Path] = None,
               extra: Optional[dict] = None) -> Path:
    """하자(보수)이행보증각서를 HWP 파일로 저장하고 경로를 반환한다."""
    period_start, period_end = _periods(extra)
    defect_start, defect_end = _defect_period(period_end)
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
    deposit = round(amount * DEFECT_RATE)

    def rep(old, new, what):
        if replace_literal_everywhere(records, old, new) != 1:
            raise WarrantyBondError(f"템플릿에서 '{what}' 자리를 찾지 못했습니다.")

    rep(_PERIOD_PLACEHOLDER, _fmt_period(period_start, period_end), "계약기간")
    rep(_AMOUNT_PLACEHOLDER, _amount_text(amount), "계약금")
    rep(_DEPOSIT_PLACEHOLDER, _amount_text(deposit), "하자보증금")
    rep(_RATE_PLACEHOLDER, f"계약금액의 {DEFECT_RATE * 100:.0f}%", "하자보증율")
    rep(_DEFECT_PERIOD_PLACEHOLDER, _fmt_defect_period(defect_start, defect_end), "하자보증기간")
    rep(_ISSUE_PLACEHOLDER, _fmt_issue(quote.issue_date), "발급일자")
    rep(_UNIV_PLACEHOLDER, quote.university, "대학명")

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
    "본사는 위 계약에 대해 납품 이후 하자보수 사항이 있거나 귀교의 하자보수 "
    "요청이 있을 경우 상기와 같은 내용을 이행할 것을 약속드립니다. 만약 "
    "본사가 귀교에 손실을 주거나, 본 계약을 이행하지 않을 경우에는 본사가 "
    "전적으로 하자보증금 상당금액을 변상할 것을 각서로 제출합니다."
)


def render_pdf(quote: Quote, out_path: str | Path, extra: Optional[dict] = None) -> Path:
    """하자(보수)이행보증각서를 PDF 파일로 저장하고 경로를 반환한다."""
    period_start, period_end = _periods(extra)
    defect_start, defect_end = _defect_period(period_end)
    bold = pdf_common.register_fonts()
    font = pdf_common.FONT
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    st = {
        "title": ParagraphStyle("title", fontName=bold, fontSize=19, alignment=TA_CENTER, leading=25),
        "item": ParagraphStyle("item", fontName=font, fontSize=12.5, alignment=TA_LEFT, leading=22),
        "legal": ParagraphStyle("legal", fontName=font, fontSize=12, alignment=TA_JUSTIFY, leading=21),
        "center": ParagraphStyle("center", fontName=font, fontSize=12.5, alignment=TA_CENTER, leading=18),
        "iss_label": ParagraphStyle("iss_label", fontName=font, fontSize=11.5, alignment=TA_LEFT, leading=16),
        "iss_value": ParagraphStyle("iss_value", fontName=font, fontSize=11.5, alignment=TA_LEFT, leading=16),
        "recipient": ParagraphStyle("recipient", fontName=bold, fontSize=13, alignment=TA_LEFT, leading=18),
    }

    left = right = 24 * mm
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=left, rightMargin=right, topMargin=22 * mm, bottomMargin=20 * mm,
        title=f"하자보수이행보증각서_{quote.university}",
    )

    subject = subject_override(quote) or _SUBJECT
    amount = quote.grand_total
    deposit = round(amount * DEFECT_RATE)

    story = []
    story.append(Paragraph("하 자 (보 수) 이 행 보 증 각 서", st["title"]))
    story.append(Spacer(1, 12 * mm))

    story.append(Paragraph(f"1. 계  약  명 : {subject}", st["item"]))
    story.append(Paragraph(f"2. 계 약 기 간 : {_fmt_period(period_start, period_end)}", st["item"]))
    story.append(Paragraph(f"3. 계  약  금 : {_amount_text(amount)}", st["item"]))
    story.append(Paragraph(f"4. 하 자 보 증 금 : {_amount_text(deposit)}", st["item"]))
    story.append(Paragraph(f"5. 하 자 보 증 율 : 계약금액의 {DEFECT_RATE * 100:.0f}%", st["item"]))
    story.append(Paragraph(f"6. 하 자 보 증 기 간 : {_fmt_defect_period(defect_start, defect_end)}", st["item"]))
    story.append(Spacer(1, 8 * mm))

    story.append(Paragraph(_LEGAL_TEXT, st["legal"]))
    story.append(Spacer(1, 12 * mm))

    story.append(Paragraph(_fmt_issue(quote.issue_date), st["center"]))
    story.append(Spacer(1, 16 * mm))

    _NBSP = "\xa0"
    ceo_value = "구 자 춘" + _NBSP * 2 + "(인)"
    issuer = Table(
        [
            [Paragraph("주  소 :", st["iss_label"]), Paragraph(f"{COMPANY.address}, 1층", st["iss_value"])],
            [Paragraph("상  호 :", st["iss_label"]), Paragraph(COMPANY.name, st["iss_value"])],
            [Paragraph("대표자 :", st["iss_label"]), Paragraph(ceo_value, st["iss_value"])],
        ],
        colWidths=[24 * mm, 138 * mm], rowHeights=8.6 * mm,
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
        value_start = 24 * mm + 2 * mm
        size = 16 * mm
        x = pdf_common.stamp_x_over(prefix, brace, value_start, font, 11.5, size)
        story.append(pdf_common.StampOverlay(str(pdf_common.STAMP_PATH), size=size,
                                             x=x, overlap=11 * mm))

    story.append(Spacer(1, 16 * mm))
    story.append(Paragraph(f"{quote.university} 귀하", st["recipient"]))

    doc.build(story)
    return out_path
