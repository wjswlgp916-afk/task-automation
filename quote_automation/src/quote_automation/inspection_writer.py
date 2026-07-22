"""검수확인서 렌더러.

바뀌는 항목:
  1. 용역명    : 견적서/대금청구서/각서와 동일 규칙 (K 단독·결합 유지, U 단독 변경)
  2. 작업기간  : 시작일 ~ 종료일 (사용자 입력, extra)
  3. 검수항목 표: 선택한 도구·부가서비스에 맞춰 행을 자동으로 골라 남긴다.
                  베이직은 검수확인서 발급 대상이 아니므로, 프리미어로 선택된
                  도구만 표에 남고, 프리미어가 하나도 없으면 오류.
  서류 발급일자: 그 서류를 발급한 날(quote.issue_date) — 작업기간과 별개.

검수항목 표는 원본 양식에서 아래처럼 고정된 행 구성을 가진다
(항상 이 순서 — 새 부가서비스가 추가되지 않는 한 안전한 위치 매핑):
    row1  K-NSSE  기본(비교분석·추세분석)
    row2  K-NSSE  부가 1 (단과대학별 비교분석)
    row3  K-NSSE  부가 2 (Peer Benchmarking)
    row4  UICA    기본(비교분석·추세분석)
    row5  UICA    부가 1 (Peer Benchmarking)
"""

from __future__ import annotations

import struct
import zlib
from datetime import date as date_cls
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

from . import cfbf, pdf_common
from .catalog import COMPANY
from .engine import Quote, subject_override, parse_token
from .hwp_writer import (
    Record,
    Cell,
    TABLE,
    LIST_HEADER,
    parse_records,
    serialize_records,
    replace_literal_everywhere,
    _split_cells,
)

# 원본 양식의 정확한 placeholder 리터럴
_SUBJECT = "학부교육의 질과 성과 진단 및 분석"
_WORK_PERIOD = "2026년   0월   0일 ~ 2026년   0월   0일"
_ISSUE = "2026년   0월  00일"
_UNIV = "00대학교"

# 검수항목 표에서 (도구, 항목) -> 템플릿 행 번호 (고정 매핑)
_TEMPLATE_ROW: Dict[Tuple[str, object], int] = {
    ("K", "base"): 1, ("K", 1): 2, ("K", 2): 3,
    ("U", "base"): 4, ("U", 1): 5,
}
_TOOL_ORDER = {"K": 0, "U": 1}


class InspectionError(RuntimeError):
    pass


def _default_template() -> Path:
    return Path(__file__).parent / "templates" / "inspection_template.hwp"


def _fmt_kor(d: date_cls) -> str:
    return f"{d.year}년 {d.month}월 {d.day}일"


def _fmt_issue(d: date_cls) -> str:
    return f"{d.year}년  {d.month}월  {d.day}일"


def _premium_selections(quote: Quote) -> List:
    """프리미어 등급 도구만 K, U 순서로 반환. 하나도 없으면 오류."""
    sels = [parse_token(c) for c in quote.source_codes]
    premium = [s for s in sels if s.grade == "P"]
    if not premium:
        raise InspectionError(
            "베이직 등급만 있어 검수확인서를 발급할 수 없습니다 "
            "(검수확인서는 프리미어 등급에만 발급합니다)."
        )
    premium.sort(key=lambda s: _TOOL_ORDER.get(s.tool, 99))
    return premium


def _work_period_text(start: date_cls, end: date_cls) -> str:
    return f"{_fmt_kor(start)} ~ {_fmt_kor(end)}"


def _dates(extra: Optional[dict]):
    extra = extra or {}
    start = extra.get("work_start")
    end = extra.get("work_end")
    if not (start and end):
        raise InspectionError("검수확인서에는 작업 시작일·종료일이 필요합니다.")
    return start, end


# --------------------------------------------------------------------------- #
# HWP
# --------------------------------------------------------------------------- #
def _find_table_by_cols(records: List[Record], ncols_want: int) -> Tuple[int, int, int]:
    for i, r in enumerate(records):
        if r.tag == TABLE:
            ncols = struct.unpack("<H", r.payload[6:8])[0]
            if ncols == ncols_want:
                level = r.level
                j = i + 1
                while j < len(records) and records[j].level >= level:
                    j += 1
                return i, j, level
    raise InspectionError(f"{ncols_want}열 표를 찾지 못했습니다.")


def _rebuild_inspection_table(records: List[Record], selections: List) -> None:
    ti, end, level = _find_table_by_cols(records, 2)
    table_rec = records[ti]
    cells = _split_cells(records[ti + 1:end], level)

    rows: Dict[int, List[Cell]] = {}
    for c in cells:
        rows.setdefault(c.row(), []).append(c)
    header_row_id = sorted(rows)[0]

    final_rows: List[List[Cell]] = [rows[header_row_id]]

    for sel in selections:
        wanted = [_TEMPLATE_ROW[(sel.tool, "base")]]
        wanted += [_TEMPLATE_ROW[(sel.tool, n)] for n in sorted(sel.addons)]

        label_cell = None
        for i, rownum in enumerate(wanted):
            by_col = {c.col(): c for c in rows[rownum]}
            if i == 0:
                label_cell = by_col[0].clone()
                content_cell = by_col[1].clone()
                final_rows.append([label_cell, content_cell])
            else:
                final_rows.append([by_col[1].clone()])
        if label_cell is not None:
            label_cell.set_rowspan(len(wanted))

    new_cells: List[Record] = []
    row_sizes: List[int] = []
    for idx, group in enumerate(final_rows):
        row_sizes.append(len(group))
        for c in group:
            c.set_row(idx)
            new_cells.extend(c.records)

    p = table_rec.payload
    old_nrows = struct.unpack("<H", p[4:6])[0]
    tail = p[18 + 2 * old_nrows:]
    new_payload = bytearray()
    new_payload += p[0:4]
    new_payload += struct.pack("<H", len(final_rows))
    new_payload += p[6:18]
    for rs in row_sizes:
        new_payload += struct.pack("<H", rs)
    new_payload += tail
    table_rec.payload = bytes(new_payload)

    records[ti + 1:end] = new_cells


def render_hwp(quote: Quote, out_path: str | Path, template: Optional[Path] = None,
               extra: Optional[dict] = None) -> Path:
    """검수확인서를 HWP 파일로 저장하고 경로를 반환한다."""
    start, end = _dates(extra)
    selections = _premium_selections(quote)

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
            raise InspectionError(f"템플릿에서 '{what}' 자리를 찾지 못했습니다.")

    rep(_WORK_PERIOD, _work_period_text(start, end), "작업기간")
    rep(_ISSUE, _fmt_issue(quote.issue_date), "발급일자")
    rep(_UNIV, quote.university, "대학명")

    subject = subject_override(quote)
    if subject:
        rep(_SUBJECT, subject, "용역명")

    _rebuild_inspection_table(records, selections)

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
    """검수확인서를 PDF 파일로 저장하고 경로를 반환한다."""
    start, end = _dates(extra)
    selections = _premium_selections(quote)

    bold = pdf_common.register_fonts()
    font = pdf_common.FONT
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    st = {
        "title": ParagraphStyle("title", fontName=bold, fontSize=20, alignment=TA_CENTER, leading=26),
        "item": ParagraphStyle("item", fontName=font, fontSize=12.5, alignment=TA_LEFT, leading=22),
        "th": ParagraphStyle("th", fontName=bold, fontSize=11.5, alignment=TA_CENTER, leading=15),
        "td_label": ParagraphStyle("td_label", fontName=font, fontSize=11, alignment=TA_CENTER, leading=15),
        "td_value": ParagraphStyle("td_value", fontName=font, fontSize=11, alignment=TA_LEFT, leading=15),
        "center": ParagraphStyle("center", fontName=font, fontSize=12.5, alignment=TA_CENTER, leading=18),
        "iss_label": ParagraphStyle("iss_label", fontName=font, fontSize=11.5, alignment=TA_LEFT, leading=16),
        "iss_value": ParagraphStyle("iss_value", fontName=font, fontSize=11.5, alignment=TA_LEFT, leading=16),
        "recipient": ParagraphStyle("recipient", fontName=bold, fontSize=13, alignment=TA_LEFT, leading=18),
    }

    left = right = 24 * mm
    content_w = A4[0] - left - right
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=left, rightMargin=right, topMargin=22 * mm, bottomMargin=20 * mm,
        title=f"검수확인서_{quote.university}",
    )

    subject = subject_override(quote) or _SUBJECT

    # 검수항목 표 데이터: (라벨 or None, 내역텍스트, span시작여부)
    _CONTENT_TEXT = {
        ("K", "base"): "대학 간·대학 내 비교분석, 연도별 추세분석",
        ("K", 1): "단과대학별 비교분석",
        ("K", 2): "Peer Benchmarking",
        ("U", "base"): "대학 간·대학 내 비교분석, 연도별 추세분석",
        ("U", 1): "Peer Benchmarking",
    }
    _LABEL_TEXT = {"K": "학부교육 실태조사(K-NSSE)", "U": "대학 혁신역량 진단조사(UICA)"}

    data = [[Paragraph("항목", st["th"]), Paragraph("내역", st["th"])]]
    span_cmds = []
    row = 1
    for sel in selections:
        keys = ["base"] + sorted(sel.addons)
        span_start = row
        for i, key in enumerate(keys):
            content = Paragraph(_CONTENT_TEXT[(sel.tool, key)], st["td_value"])
            if i == 0:
                data.append([Paragraph(_LABEL_TEXT[sel.tool], st["td_label"]), content])
            else:
                data.append(["", content])
            row += 1
        if len(keys) > 1:
            span_cmds.append(("SPAN", (0, span_start), (0, span_start + len(keys) - 1)))

    story = []
    story.append(Paragraph("검 수 확 인 서", st["title"]))
    story.append(Spacer(1, 12 * mm))

    story.append(Paragraph(f"1. 용    역    명 : {subject}", st["item"]))
    story.append(Paragraph(
        f"2. 작  업  기  간 : {_work_period_text(start, end)}", st["item"]))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("3. 검  수  항  목 : ", st["item"]))
    story.append(Spacer(1, 2 * mm))

    tbl = Table(data, colWidths=[55 * mm, content_w - 55 * mm], rowHeights=9 * mm)
    tbl.hAlign = "LEFT"
    style = [
        ("BOX", (0, 0), (-1, -1), 1.0, colors.black),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f2f2")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    style.extend(span_cmds)
    tbl.setStyle(TableStyle(style))
    story.append(tbl)
    story.append(Spacer(1, 10 * mm))

    story.append(Paragraph("상기 작업이 정상적으로 완료되었음을 확인합니다.", st["center"]))
    story.append(Spacer(1, 10 * mm))

    story.append(Paragraph(_fmt_issue(quote.issue_date), st["center"]))
    story.append(Spacer(1, 16 * mm))

    _NBSP = " "
    ceo_value = "구 자 춘" + _NBSP * 10 + "(인)"
    indent_w = 55 * mm     # 원본처럼 오른쪽으로 들여쓰기 (빈 열로 처리)
    label_w = 24 * mm
    issuer = Table(
        [
            ["", Paragraph("상    호:", st["iss_label"]), Paragraph(COMPANY.name, st["iss_value"])],
            ["", Paragraph("주    소:", st["iss_label"]), Paragraph(COMPANY.address, st["iss_value"])],
            ["", Paragraph("대 표 자:", st["iss_label"]), Paragraph(ceo_value, st["iss_value"])],
        ],
        colWidths=[indent_w, label_w, content_w - indent_w - label_w], rowHeights=8.6 * mm,
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
        value_start = indent_w + label_w + 2
        size = 18 * mm
        x = pdf_common.stamp_x_over(prefix, brace, value_start, font, 11.5, size)
        story.append(pdf_common.StampOverlay(str(pdf_common.STAMP_PATH), size=size,
                                             x=x, overlap=13 * mm))

    story.append(Spacer(1, 16 * mm))
    story.append(Paragraph(f"{quote.university} 귀하", st["recipient"]))

    doc.build(story)
    return out_path
