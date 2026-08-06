"""청렴계약서(서약서) 렌더러.

바뀌는 항목:
  1. 대학명 : 수신 문구("OO대학교 총장 귀하") 1곳.
  2. 서약일자 : 하단 "2026.   .   ." 자리. 서류 발급일자(quote.issue_date)로
                자동 채운다(정보보안/개인정보 서약서와 같은 성격 — 서약서를
                제출하는 날짜라 계약서의 계약체결일과 달리 대학이 아니라
                우리가 정한다).

본문 청렴계약 조건 3개 항목과 서약자(배상훈, 교육과미래연구소 소장) 정보,
직인은 대학과 무관하게 고정이라 원본 그대로 둔다. 원본 양식에 메모·형광펜·
글자모양 음영 모두 없음(확인 완료).
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
from .engine import Quote
from .hwp_writer import (
    parse_records, serialize_records, replace_literal_everywhere, strip_memo_controls,
    strip_highlight_ranges,
)

# 원본 양식의 정확한 placeholder 리터럴
_UNIV_PLACEHOLDER = "OO대학교"
_DATE_PLACEHOLDER = "2026.   .   ."

_STAMP_BAE_PATH = Path(__file__).parent / "templates" / "stamp_bae.png"


class IntegrityPledgeError(RuntimeError):
    pass


def _default_template() -> Path:
    return Path(__file__).parent / "templates" / "integrity_pledge_template.hwp"


def _fmt_issue(d: date_cls) -> str:
    return f"{d.year}.   {d.month}.   {d.day}."


def render_hwp(quote: Quote, out_path: str | Path, template: Optional[Path] = None,
               extra: Optional[dict] = None) -> Path:
    """청렴계약서(서약서)를 HWP 파일로 저장하고 경로를 반환한다."""
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

    def rep(old, new, what):
        if replace_literal_everywhere(records, old, new) != 1:
            raise IntegrityPledgeError(f"템플릿에서 '{what}' 자리를 찾지 못했습니다.")

    rep(_DATE_PLACEHOLDER, _fmt_issue(quote.issue_date), "서약일자")
    if replace_literal_everywhere(records, _UNIV_PLACEHOLDER, quote.university) < 1:
        raise IntegrityPledgeError("양식에서 대학명 자리를 찾지 못했습니다.")

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
_INTRO_TEXT = (
    "「국가계약법」제5조의2에 따라 본 입찰에 참여한 당사 대리인과 임직원은 "
    "입찰·낙찰, 계약체결 또는 계약이행 등의 과정(준공·납품 이후를 포함한다)"
    "에서 아래 각 호의 청렴계약 조건을 준수할 것이며, 이를 위반할 때에는 "
    "낙찰취소 등을 당하거나 계약의 해제·해지를 당하는 등의 불이익을 감수하고, "
    "이에 민·형사상 이의를 제기하지 않을 것임을 약정합니다."
)
_PLEDGE_ITEMS = (
    # 授受(수수) 한자는 PDF 서체(나눔명조)에 글리프가 없어 깨져 보이므로
    # PDF 본문에서만 한글만 남긴다(HWP 는 원본 그대로라 영향 없음).
    "금품·향응 등을 요구 또는 약속하거나 수수하지 않을 것이며, 관계공무원에게 "
    "금품·향응 등을 제공한 경우에는 「국가계약법 시행령」제76조제1항제10호에 따른 "
    "부정당업자의 입찰참가자격 제한 처분을 받겠습니다.",
    "입찰가격의 사전 협의 또는 특정인의 낙찰을 위한 담합 등 공정한 경쟁을 방해하는 "
    "행위 시에는 「국가계약법 시행령」제76조제1항제7호에 따른 부정당업자의 입찰참가자격 "
    "제한 처분을 받겠습니다.",
    "공정한 직무수행을 방해하는 알선·청탁을 통하여 입찰 또는 계약과 관련된 특정 정보의 "
    "제공을 요구하거나 받는 행위를 하지 않겠습니다.",
)


def render_pdf(quote: Quote, out_path: str | Path, extra: Optional[dict] = None) -> Path:
    """청렴계약서(서약서)를 PDF 파일로 저장하고 경로를 반환한다."""
    bold = pdf_common.register_fonts()
    font = pdf_common.FONT
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    st = {
        "title": ParagraphStyle("title", fontName=bold, fontSize=20, alignment=TA_CENTER, leading=26),
        "body": ParagraphStyle("body", fontName=font, fontSize=12, alignment=TA_JUSTIFY, leading=21),
        "item": ParagraphStyle("item", fontName=font, fontSize=11.5, alignment=TA_JUSTIFY, leading=19,
                               leftIndent=6 * mm, firstLineIndent=-6 * mm),
        "center": ParagraphStyle("center", fontName=font, fontSize=12.5, alignment=TA_CENTER, leading=18),
        "iss_label": ParagraphStyle("iss_label", fontName=font, fontSize=11.5, alignment=TA_LEFT, leading=16),
        "iss_value": ParagraphStyle("iss_value", fontName=font, fontSize=11.5, alignment=TA_LEFT, leading=16),
        "recipient": ParagraphStyle("recipient", fontName=bold, fontSize=13, alignment=TA_LEFT, leading=18),
    }

    left = right = 24 * mm
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=left, rightMargin=right, topMargin=22 * mm, bottomMargin=20 * mm,
        title=f"청렴계약서_{quote.university}",
    )

    univ = quote.university

    story = []
    story.append(Paragraph("청 렴 계 약 서 (서 약 서)", st["title"]))
    story.append(Spacer(1, 12 * mm))

    story.append(Paragraph(_INTRO_TEXT, st["body"]))
    story.append(Spacer(1, 6 * mm))

    for n, text in enumerate(_PLEDGE_ITEMS, start=1):
        story.append(Paragraph(f"{n}. {text}", st["item"]))
        story.append(Spacer(1, 2 * mm))
    story.append(Spacer(1, 10 * mm))

    story.append(Paragraph(_fmt_issue(quote.issue_date), st["center"]))
    story.append(Spacer(1, 14 * mm))

    _NBSP = "\xa0"
    name_value = "배 상 훈" + _NBSP * 12 + "(인)"
    table = Table(
        [
            [Paragraph("서약자", st["iss_label"]), Paragraph("소  속 :", st["iss_label"]),
             Paragraph("성균관대학교 교육과 미래연구소", st["iss_value"])],
            ["", Paragraph("직  급 :", st["iss_label"]), Paragraph("소장", st["iss_value"])],
            ["", Paragraph("성  명 :", st["iss_label"]), Paragraph(name_value, st["iss_value"])],
        ],
        colWidths=[24 * mm, 20 * mm, 118 * mm], rowHeights=8.6 * mm,
    )
    table.hAlign = "LEFT"
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(table)

    if _STAMP_BAE_PATH.exists():
        brace_at = name_value.find("(")
        prefix = name_value[:brace_at]
        brace = name_value[brace_at:]
        value_start = 24 * mm + 20 * mm + 2 * mm
        size = 18 * mm
        x = pdf_common.stamp_x_over(prefix, brace, value_start, font, 11.5, size)
        story.append(pdf_common.StampOverlay(str(_STAMP_BAE_PATH), size=size,
                                             x=x, overlap=13 * mm, mask="auto"))

    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(f"{univ} 총장 귀하", st["recipient"]))

    doc.build(story)
    return out_path
