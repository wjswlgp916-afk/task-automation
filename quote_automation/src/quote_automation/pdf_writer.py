"""견적서 PDF 렌더러 (reportlab).

원본 한글 양식의 배치를 재현해 대학에 바로 보낼 수 있는 PDF 를 만든다.
설문도구/부가서비스 개수에 따라 품목 테이블 행 수가 자동으로 늘고 준다.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
)
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT

from .catalog import COMPANY
from .engine import Quote


# --------------------------------------------------------------------------- #
# 한글 폰트 등록 (NanumGothic)
# --------------------------------------------------------------------------- #
_FONT = "NanumGothic"
_FONT_BOLD = "NanumGothic-Bold"
_FONT_PATHS = [
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", _FONT),
    ("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", _FONT_BOLD),
]


def _register_fonts() -> None:
    for path, name in _FONT_PATHS:
        if name not in pdfmetrics.getRegisteredFontNames() and Path(path).exists():
            pdfmetrics.registerFont(TTFont(name, path))
    # 폰트가 없으면 기본 폰트로 대체
    if _FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFontFamily(_FONT, normal="Helvetica")


def _num(v: int) -> str:
    return f"{v:,}" if v else "0"


def render_pdf(quote: Quote, out_path: str | Path) -> Path:
    """견적서를 PDF 파일로 저장하고 경로를 반환한다."""
    _register_fonts()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    bold = _FONT_BOLD if _FONT_BOLD in pdfmetrics.getRegisteredFontNames() else _FONT

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"견적서_{quote.university}",
    )

    styles = _styles(bold)
    story = []

    # ---- 제목 --------------------------------------------------------------
    story.append(Paragraph("견 적 서", styles["title"]))
    story.append(Spacer(1, 6 * mm))

    # ---- 상단: 좌(수신/일자) · 우(공급자 정보) ----------------------------
    story.append(_header_table(quote, styles))
    story.append(Spacer(1, 5 * mm))

    story.append(Paragraph("아래와 같이 견적합니다.", styles["normal"]))
    story.append(Spacer(1, 3 * mm))

    # ---- 합계금액 요약 ----------------------------------------------------
    story.append(_summary_table(quote, styles))
    story.append(Spacer(1, 2 * mm))

    # ---- 품목 테이블 ------------------------------------------------------
    story.append(_items_table(quote, styles))

    doc.build(story)
    return out_path


# --------------------------------------------------------------------------- #
# 구성 요소
# --------------------------------------------------------------------------- #
def _styles(bold: str) -> dict:
    return {
        "title": ParagraphStyle(
            "title", fontName=bold, fontSize=26, alignment=TA_CENTER,
            spaceAfter=0, leading=30,
        ),
        "normal": ParagraphStyle(
            "normal", fontName=_FONT, fontSize=10, alignment=TA_LEFT, leading=14,
        ),
        "cell": ParagraphStyle(
            "cell", fontName=_FONT, fontSize=9, alignment=TA_CENTER, leading=12,
        ),
        "cell_left": ParagraphStyle(
            "cell_left", fontName=_FONT, fontSize=9, alignment=TA_LEFT, leading=12,
        ),
        "small": ParagraphStyle(
            "small", fontName=_FONT, fontSize=8, alignment=TA_CENTER, leading=11,
        ),
    }


def _header_table(quote: Quote, styles: dict) -> Table:
    d = quote.issue_date
    date_str = f"서기   {d.year}년    {d.month:02d}월    {d.day:02d}일"

    # 좌측: 발급일자 + 수신처
    left = Table(
        [[Paragraph(date_str, styles["normal"])],
         [Spacer(1, 8 * mm)],
         [Paragraph(f"{quote.university}  귀중", styles["normal"])]],
        colWidths=[80 * mm],
    )
    left.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTNAME", (0, 0), (-1, -1), _FONT),
        ("BOTTOMPADDING", (0, 2), (0, 2), 0),
    ]))

    # 우측: 공급자 정보 박스
    c = COMPANY
    supplier = Table(
        [
            ["등록번호", c.registration_no],
            ["상호", c.name],
            ["대표자", c.ceo],
            ["소재지", c.address],
            ["업태", c.business_type],
            ["종목", c.business_item],
            ["TEL", c.tel],
        ],
        colWidths=[18 * mm, 66 * mm],
    )
    supplier.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), _FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))

    wrap = Table([[left, supplier]], colWidths=[84 * mm, 90 * mm])
    wrap.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return wrap


def _summary_table(quote: Quote, styles: dict) -> Table:
    text = f"{quote.korean_amount}(  {quote.total_won}  )"
    t = Table(
        [[Paragraph("합계금액<br/>(공급가액+VAT)", styles["cell"]),
          Paragraph(text, styles["cell"])]],
        colWidths=[45 * mm, 129 * mm],
    )
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), _FONT),
        ("GRID", (0, 0), (-1, -1), 0.7, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (0, 0), colors.whitesmoke),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _items_table(quote: Quote, styles: dict) -> Table:
    header = ["품 명", "규격", "수량", "단 가", "공급가액", "세 액", "비 고"]
    rows = [[Paragraph(h, styles["cell"]) for h in header]]

    span_cmds = []
    row_idx = 1
    for item in quote.items:
        if item.kind == "main":
            name = Paragraph(item.name, styles["cell_left"])
        else:
            # 부가서비스: 품명 칸을 조금 들여쓰기
            name = Paragraph("　" + item.name, styles["cell_left"])
        rows.append([
            name,
            Paragraph(item.spec, styles["cell"]),
            Paragraph(str(item.qty), styles["cell"]),
            Paragraph(_num(item.unit_price), styles["cell"]),
            Paragraph(_num(item.supply), styles["cell"]),
            Paragraph(_num(item.vat), styles["cell"]),
            Paragraph("", styles["cell"]),
        ])
        row_idx += 1

    # 합계 행
    rows.append([
        Paragraph("합 계", styles["cell"]),
        "", "", "",
        Paragraph(_num(quote.total_supply), styles["cell"]),
        Paragraph(_num(quote.total_vat), styles["cell"]),
        Paragraph("", styles["cell"]),
    ])
    total_row = len(rows) - 1
    span_cmds.append(("SPAN", (0, total_row), (3, total_row)))

    col_widths = [58 * mm, 20 * mm, 12 * mm, 24 * mm, 24 * mm, 20 * mm, 16 * mm]
    t = Table(rows, colWidths=col_widths, repeatRows=1)
    style = [
        ("FONTNAME", (0, 0), (-1, -1), _FONT),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
        ("BACKGROUND", (0, total_row), (-1, total_row), colors.whitesmoke),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    style.extend(span_cmds)
    t.setStyle(TableStyle(style))
    return t
