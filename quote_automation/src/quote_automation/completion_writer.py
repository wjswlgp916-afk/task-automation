"""완료계 렌더러.

바뀌는 항목:
  1. 용 역 명   : 견적서/대금청구서/각서/검수확인서와 동일 규칙
                  (K 단독·결합 유지, U 단독 변경)
  2. 계약금액   : 견적 합계와 자동 연동 -> "금 이백이십만원(￦ 2,200,000 )"
                  (다른 서류들과 같은 한글 금액 표기)
  3. 계약년월일 : 대학마다 다르지만 보통 9월 1일 (기본값, 필요시 수정)
  4. 착수년월일 : 보통 고정, 기본값 있음 (사용자 입력, extra, 오버라이드 가능)
  5. 완료기한   : 보통 고정, 기본값 있음 (사용자 입력, extra, 오버라이드 가능)
  6. 완료년월일 : 보통 고정, 기본값 있음 (사용자 입력, extra, 오버라이드 가능)
  서류 발급일자: 그 서류를 발급한 날(quote.issue_date) — 위 날짜들과 별개.

계약년월일·착수년월일은 원본 양식에서 완전히 같은 문구("2026년   0월   0일")로
비어있어, 문서 전체를 훑는 replace_literal_everywhere 로는 두 자리를 구분할 수
없다. 각 줄이 라벨과 값을 한 문단에 담고 있으므로, 라벨 문자열로 문단을 먼저
찾은 뒤 그 문단 안에서만 값을 치환한다 (_replace_in_labeled_paragraph).
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
    PARA_HEADER,
    PARA_TEXT,
    parse_records,
    serialize_records,
    replace_literal,
    replace_literal_everywhere,
    strip_memo_controls,
    strip_highlight_ranges,
    text_of,
)
from .korean_num import number_to_korean_plain

# 원본 양식의 정확한 placeholder 리터럴
_SUBJECT = "학부교육의 질과 성과 진단 및 분석"
_AMOUNT_PLACEHOLDER = "금 00000만 원(￦ 0,000,000 )"
_DATE_PLACEHOLDER = "2026년   0월   0일"          # 계약년월일·착수년월일 공통
_DEADLINE_PLACEHOLDER = "2027년   01월   31일"     # 완료기한
_COMPLETION_PLACEHOLDER = "2026년   12월   18일"   # 완료년월일
_ISSUE_PLACEHOLDER = "2026년   0월  00일"
_UNIV_PLACEHOLDER = "00대학교"

_CONTRACT_LABEL = "계 약 년 월 일"
_COMMENCEMENT_LABEL = "착 수 년 월 일"
_DEADLINE_LABEL = "완  료  기  한"      # 글자 사이 두 칸 (완료년월일 라벨과 구분)
_COMPLETION_LABEL = "완 료 년 월 일"    # 글자 사이 한 칸

DEFAULT_CONTRACT_DATE = date_cls(2026, 9, 1)
DEFAULT_COMMENCEMENT = date_cls(2026, 9, 1)
DEFAULT_DEADLINE = date_cls(2027, 1, 31)
DEFAULT_COMPLETION = date_cls(2026, 12, 18)


class CompletionError(RuntimeError):
    pass


def _default_template() -> Path:
    return Path(__file__).parent / "templates" / "completion_report_template.hwp"


def _fmt_body(d: date_cls) -> str:
    return f"{d.year}년   {d.month:02d}월   {d.day:02d}일"


def _fmt_issue(d: date_cls) -> str:
    return f"{d.year}년   {d.month:02d}월  {d.day:02d}일"


def _man_amount_text(amount: int) -> str:
    """다른 서류들과 같은 한글 금액 표기: '금 이백이십만원(￦ 2,200,000 )'."""
    kor = number_to_korean_plain(amount)
    return f"금 {kor}원(￦ {amount:,} )"


def _dates(extra: Optional[dict]):
    extra = extra or {}
    contract_date = extra.get("contract_date") or DEFAULT_CONTRACT_DATE
    commencement_date = extra.get("commencement_date") or DEFAULT_COMMENCEMENT
    completion_deadline = extra.get("completion_deadline") or DEFAULT_DEADLINE
    completion_date = extra.get("completion_date") or DEFAULT_COMPLETION
    return contract_date, commencement_date, completion_deadline, completion_date


def _prev_para_header(records, txt_idx: int):
    for j in range(txt_idx - 1, -1, -1):
        if records[j].tag == PARA_HEADER:
            return records[j]
        if records[j].tag == PARA_TEXT:
            return None
    return None


def _replace_in_labeled_paragraph(records, label: str, old: str, new: str, what: str) -> None:
    """``label`` 이 들어있는 문단을 찾아, 그 문단 안에서만 ``old`` 를 치환한다.

    계약년월일/착수년월일처럼 서로 다른 문단인데 값 부분 문구가 완전히
    같은 경우, 문서 전체를 훑는 치환으로는 두 자리를 구분할 수 없어 이 방식을 쓴다.
    """
    for i, r in enumerate(records):
        if r.tag == PARA_TEXT and label in text_of(r):
            hdr = _prev_para_header(records, i)
            if hdr and replace_literal(hdr, r, old, new):
                return
    raise CompletionError(f"템플릿에서 '{what}' 자리를 찾지 못했습니다.")


def render_hwp(quote: Quote, out_path: str | Path, template: Optional[Path] = None,
               extra: Optional[dict] = None) -> Path:
    """완료계를 HWP 파일로 저장하고 경로를 반환한다."""
    contract_date, commencement_date, completion_deadline, completion_date = _dates(extra)
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

    _replace_in_labeled_paragraph(
        records, _CONTRACT_LABEL, _DATE_PLACEHOLDER, _fmt_body(contract_date), "계약년월일")
    _replace_in_labeled_paragraph(
        records, _COMMENCEMENT_LABEL, _DATE_PLACEHOLDER, _fmt_body(commencement_date), "착수년월일")
    _replace_in_labeled_paragraph(
        records, _DEADLINE_LABEL, _DEADLINE_PLACEHOLDER, _fmt_body(completion_deadline), "완료기한")
    _replace_in_labeled_paragraph(
        records, _COMPLETION_LABEL, _COMPLETION_PLACEHOLDER, _fmt_body(completion_date), "완료년월일")

    def rep(old, new, what):
        if replace_literal_everywhere(records, old, new) != 1:
            raise CompletionError(f"템플릿에서 '{what}' 자리를 찾지 못했습니다.")

    rep(_AMOUNT_PLACEHOLDER, _man_amount_text(amount), "계약금액")
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
    """완료계를 PDF 파일로 저장하고 경로를 반환한다."""
    contract_date, commencement_date, completion_deadline, completion_date = _dates(extra)
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
        title=f"완료계_{quote.university}",
    )

    subject = subject_override(quote) or _SUBJECT
    amount = quote.grand_total

    story = []
    story.append(Paragraph("완 료 계", st["title"]))
    story.append(Spacer(1, 12 * mm))

    story.append(Paragraph(f"1. 용  역  명 : {subject}", st["item"]))
    story.append(Paragraph(f"2. 계약금액 : (VAT 포함) {_man_amount_text(amount)}", st["item"]))
    story.append(Paragraph(f"3. 계약년월일 : {_fmt_body(contract_date)}", st["item"]))
    story.append(Paragraph(f"4. 착수년월일 : {_fmt_body(commencement_date)}", st["item"]))
    story.append(Paragraph(f"5. 완료기한 : {_fmt_body(completion_deadline)}", st["item"]))
    story.append(Paragraph(f"6. 완료년월일 : {_fmt_body(completion_date)}", st["item"]))
    story.append(Spacer(1, 8 * mm))

    story.append(Paragraph("위와 같이 완료계를 제출합니다.", st["center"]))
    story.append(Spacer(1, 12 * mm))

    story.append(Paragraph(_fmt_issue(quote.issue_date), st["center"]))
    story.append(Spacer(1, 16 * mm))

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

    if pdf_common.STAMP_PATH.exists():
        brace_at = ceo_value.find("(")
        prefix, brace = ceo_value[:brace_at], ceo_value[brace_at:]
        value_start = 18 * mm + 20 * mm + 2 * mm
        size = 18 * mm
        x = pdf_common.stamp_x_over(prefix, brace, value_start, font, 11.5, size)
        story.append(pdf_common.StampOverlay(str(pdf_common.STAMP_PATH), size=size,
                                             x=x, overlap=13 * mm))

    story.append(Spacer(1, 16 * mm))
    story.append(Paragraph(f"{quote.university} 총장 귀하", st["recipient"]))

    doc.build(story)
    return out_path
