"""정보보안 / 개인정보 서약서 렌더러.

바뀌는 항목:
  1. 자문 시작일 : 본문 문장 "OOOO년 O월 O일부로" 자리. 계약서의 자문
                    시작일(period_start)과 같은 개념이라 extra 키를
                    공유한다(기본값 2026-09-01).
  2. 계약명(자문 내용) : 견적서 등과 동일 규칙(K 단독·결합 유지, U 단독 변경).
  3. 대학명       : 본문 문장 + 수신 문구("OO대학교 총장 귀하") 2곳.

원본 양식 끝의 "20  년   월   일"(서명 날짜)은 서류 발급일자(quote.issue_date)로
자동 채운다. 업체 서약자(구자춘)·학교 서약집행자(배상훈) 정보와 직인은
대학과 무관하게 고정이라 원본 그대로 둔다.

본문 문장은 원래 2줄(LINE_SEG 2개)로 나뉘어 있어, 값을 채운 뒤 반드시
낡은 LINE_SEG 를 정리해야 한다(문서 보안설정 [높음] 대응, CLAUDE.md 0번
규칙 참고).
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
from .engine import Quote, subject_override
from .hwp_writer import (
    PARA_HEADER,
    PARA_TEXT,
    Record,
    clear_stale_line_seg,
    parse_records,
    serialize_records,
    replace_literal_everywhere,
    text_of,
)

# 원본 양식의 정확한 placeholder 리터럴
_SUBJECT = "학부교육의 질과 성과 진단 및 분석"
_PERIOD_START_PLACEHOLDER = "2026년 0월 0일"
_ISSUE_PLACEHOLDER = "20 년  월   일"
_UNIV = "OO대학교"

_STAMP_BAE_PATH = Path(__file__).parent / "templates" / "stamp_bae.png"

DEFAULT_PERIOD_START = date_cls(2026, 9, 1)


class SecurityPledgeError(RuntimeError):
    pass


def _default_template() -> Path:
    return Path(__file__).parent / "templates" / "security_pledge_template.hwp"


def _fmt_body(d: date_cls) -> str:
    return f"{d.year}년 {d.month}월 {d.day}일"


def _fmt_issue(d: date_cls) -> str:
    return f"{d.year}년   {d.month}월   {d.day}일"


def _period_start(extra: Optional[dict]) -> date_cls:
    extra = extra or {}
    return extra.get("period_start") or DEFAULT_PERIOD_START


def _find_para(records, needle: str):
    for i, r in enumerate(records):
        if r.tag == PARA_TEXT and needle in text_of(r):
            for j in range(i - 1, -1, -1):
                if records[j].tag == PARA_HEADER:
                    return j, i
                if records[j].tag == PARA_TEXT:
                    break
    raise SecurityPledgeError(f"양식에서 '{needle}' 문단을 찾지 못했습니다.")


def render_hwp(quote: Quote, out_path: str | Path, template: Optional[Path] = None,
               extra: Optional[dict] = None) -> Path:
    """정보보안/개인정보 서약서를 HWP 파일로 저장하고 경로를 반환한다."""
    period_start = _period_start(extra)
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

    def rep(old, new, what):
        if replace_literal_everywhere(records, old, new) != 1:
            raise SecurityPledgeError(f"템플릿에서 '{what}' 자리를 찾지 못했습니다.")

    # 본문 서약 문장 안 자문 시작일 · 대학명 · 계약명(값을 바꾼 뒤 낡은
    # LINE_SEG 정리 — 이 문단은 원래 2줄로 나뉘어 있었다).
    hdr_idx, _ = _find_para(records, "부로")
    rep(_PERIOD_START_PLACEHOLDER, _fmt_body(period_start), "자문 시작일")
    subject = subject_override(quote)
    if subject:
        rep(_SUBJECT, subject, "계약명")
    if replace_literal_everywhere(records, _UNIV, quote.university) < 1:
        raise SecurityPledgeError("양식에서 대학명 자리를 찾지 못했습니다.")
    clear_stale_line_seg(records, hdr_idx)

    # 서명 날짜 자리 -> 서류 발급일자로 자동 채움.
    issue_hdr_idx, _ = _find_para(records, _ISSUE_PLACEHOLDER)
    rep(_ISSUE_PLACEHOLDER, _fmt_issue(quote.issue_date), "서명일자")
    clear_stale_line_seg(records, issue_hdr_idx)

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
_PLEDGE_ITEMS = (
    "본인은 비밀, 행정문서, 중요정보, 개인정보 등 비공개자료의 열람·취급 후 "
    "OO대학교 규정을 준수하겠습니다.",
    "본인은 업무취급 시 지득한 사실에 대하여는 대인관계에 있어서나 장소여하를 "
    "막론하고 누설 및 공개하지 않겠습니다.",
    "본인이 이를 위반 시에는 관계법규를 포함한 어떤 법적 책임 등의 조치에도 "
    "따르겠습니다.",
)


def render_pdf(quote: Quote, out_path: str | Path, extra: Optional[dict] = None) -> Path:
    """정보보안/개인정보 서약서를 PDF 파일로 저장하고 경로를 반환한다."""
    period_start = _period_start(extra)
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
        title=f"정보보안개인정보서약서_{quote.university}",
    )

    subject = subject_override(quote) or _SUBJECT
    univ = quote.university

    story = []
    story.append(Paragraph("정보보안 / 개인정보 서약서", st["title"]))
    story.append(Spacer(1, 12 * mm))

    story.append(Paragraph(
        f"본인은 {_fmt_body(period_start)}부로 {univ} {subject} 자문을 수행함에 있어 "
        "다음 사항을 준수할 것을 엄숙히 서약합니다.", st["body"]))
    story.append(Spacer(1, 6 * mm))

    for n, text in enumerate(_PLEDGE_ITEMS, start=1):
        story.append(Paragraph(f"{n}. {text.replace('OO대학교', univ)}", st["item"]))
        story.append(Spacer(1, 2 * mm))
    story.append(Spacer(1, 10 * mm))

    story.append(Paragraph(_fmt_issue(quote.issue_date), st["center"]))
    story.append(Spacer(1, 14 * mm))

    _NBSP = "\xa0"

    def _signer_block(role: str, org: str, title: str, name: str,
                      stamp_path: Path, stamp_mask=pdf_common.STAMP_WHITE_MASK) -> None:
        name_value = name + _NBSP * 12 + "(서명/인)"
        table = Table(
            [
                [Paragraph(role, st["iss_label"]), Paragraph("소  속 :", st["iss_label"]),
                 Paragraph(org, st["iss_value"])],
                ["", Paragraph("직  급 :", st["iss_label"]), Paragraph(title, st["iss_value"])],
                ["", Paragraph("성  명 :", st["iss_label"]), Paragraph(name_value, st["iss_value"])],
            ],
            colWidths=[32 * mm, 20 * mm, 110 * mm], rowHeights=8.6 * mm,
        )
        table.hAlign = "LEFT"
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(table)
        if stamp_path.exists():
            brace_at = name_value.find("(")
            prefix = name_value[:brace_at]
            brace = name_value[brace_at:]
            value_start = 32 * mm + 20 * mm + 2 * mm
            size = 18 * mm
            x = pdf_common.stamp_x_over(prefix, brace, value_start, font, 11.5, size)
            story.append(pdf_common.StampOverlay(str(stamp_path), size=size,
                                                 x=x, overlap=13 * mm, mask=stamp_mask))
        story.append(Spacer(1, 6 * mm))

    _signer_block("업체 서약자", "성균관대학교 산학협력단", "단장", "구 자 춘",
                  pdf_common.STAMP_PATH)
    _signer_block("학교 서약집행자", "교육과미래연구소", "소장", "배 상 훈",
                  _STAMP_BAE_PATH, stamp_mask="auto")

    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(f"{univ} 총장 귀하", st["recipient"]))

    doc.build(story)
    return out_path
