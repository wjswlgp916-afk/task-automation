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
    Image,
)
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT

from .catalog import COMPANY
from .engine import Quote

_STAMP_PATH = Path(__file__).parent / "templates" / "stamp.png"


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
    story.append(Spacer(1, 6 * mm))

    story.append(Paragraph("아래와 같이 견적합니다.", styles["greeting"]))
    story.append(Spacer(1, 4 * mm))

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
            "normal", fontName=_FONT, fontSize=11, alignment=TA_LEFT, leading=15,
        ),
        "date_line": ParagraphStyle(
            "date_line", fontName=_FONT, fontSize=13, alignment=TA_LEFT, leading=18,
        ),
        "univ_line": ParagraphStyle(
            "univ_line", fontName=bold, fontSize=15, alignment=TA_LEFT,
            leading=20, leftIndent=10 * mm,
        ),
        "greeting": ParagraphStyle(
            "greeting", fontName=bold, fontSize=15, alignment=TA_LEFT, leading=20,
        ),
        "cell": ParagraphStyle(
            "cell", fontName=_FONT, fontSize=10, alignment=TA_CENTER, leading=13,
        ),
        "cell_left": ParagraphStyle(
            "cell_left", fontName=_FONT, fontSize=10, alignment=TA_LEFT, leading=13,
        ),
        "cell_addon": ParagraphStyle(
            "cell_addon", fontName=_FONT, fontSize=9.5, alignment=TA_LEFT,
            leading=12, textColor=colors.HexColor("#555555"), leftIndent=10,
        ),
        "supplier_label": ParagraphStyle(
            "supplier_label", fontName=bold, fontSize=9, alignment=TA_CENTER,
            leading=12,
        ),
        "supplier_value": ParagraphStyle(
            "supplier_value", fontName=_FONT, fontSize=9, alignment=TA_LEFT,
            leading=12,
        ),
        "small": ParagraphStyle(
            "small", fontName=_FONT, fontSize=9, alignment=TA_CENTER, leading=12,
        ),
    }


def _header_table(quote: Quote, styles: dict) -> Table:
    d = quote.issue_date
    date_str = f"서기   {d.year}년    {d.month:02d}월    {d.day:02d}일"

    # 좌측: 발급일자 + 수신처
    # (표의 서로 다른 행에 걸친 문단은 spaceBefore 가 적용되지 않으므로,
    #  행 간 간격은 셀 패딩으로 직접 준다)
    left = Table(
        [[Paragraph(date_str, styles["date_line"])],
         [Paragraph(f"{quote.university} 귀중", styles["univ_line"])]],
        colWidths=[68 * mm],
    )
    left.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (0, 0), 0),
        ("BOTTOMPADDING", (0, 0), (0, 0), 0),
        ("TOPPADDING", (0, 1), (0, 1), 6 * mm),
        ("BOTTOMPADDING", (0, 1), (0, 1), 0),
    ]))

    # 우측: 공급자 정보 박스 (원본 양식과 동일하게 5행 구조:
    # 등록번호/소재지/TEL 은 단독 행, 상호+대표자·업태+종목 은 한 행에 나란히)
    c = COMPANY
    ceo_name = c.ceo.replace("(인)", "").strip()
    if _STAMP_PATH.exists():
        stamp_img = Image(str(_STAMP_PATH), width=13 * mm, height=13 * mm)
        ceo_cell = Table(
            [[Paragraph(ceo_name, styles["supplier_value"]), stamp_img]],
            colWidths=[14 * mm, 14 * mm],
        )
        ceo_cell.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
    else:
        ceo_cell = Paragraph(c.ceo, styles["supplier_value"])

    L, V = styles["supplier_label"], styles["supplier_value"]
    col_widths = [20 * mm, 40 * mm, 16 * mm, 30 * mm]     # 합계 106mm
    data = [
        ["등록번호", Paragraph(c.registration_no, V), "", ""],
        ["상호", Paragraph(c.name, V), "대표자", ceo_cell],
        ["소재지", Paragraph(c.address, V), "", ""],
        ["업태", Paragraph(c.business_type, V), "종목", Paragraph(c.business_item, V)],
        ["TEL", Paragraph(c.tel, V), "", ""],
    ]
    data = [[Paragraph(row[0], L)] + row[1:] for row in data]
    for row in data:
        if row[2] != "":
            row[2] = Paragraph(row[2], L) if isinstance(row[2], str) else row[2]

    row_heights = [8.4 * mm, 15 * mm, 8.4 * mm, 8.4 * mm, 8.4 * mm]
    supplier = Table(data, colWidths=col_widths, rowHeights=row_heights)
    span_rows = (0, 2, 4)   # 등록번호 / 소재지 / TEL: 값 칸이 나머지 3칸을 모두 차지
    style = [
        ("BOX", (0, 0), (-1, -1), 1.0, colors.black),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    for r in span_rows:
        style.append(("SPAN", (1, r), (3, r)))
    supplier.setStyle(TableStyle(style))

    # 좌측 블록(날짜+대학명)은 우측 정보표보다 짧아 위쪽에만 붙어 있으면
    # 허공에 떠 보이므로, 정보표 높이 전체를 기준으로 세로 가운데 정렬한다.
    wrap = Table([[left, supplier]], colWidths=[68 * mm, 106 * mm])
    wrap.setStyle(TableStyle([
        ("VALIGN", (0, 0), (0, 0), "MIDDLE"),
        ("VALIGN", (1, 0), (1, 0), "TOP"),
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
    addon_rows = []
    for item in quote.items:
        if item.kind == "main":
            name = Paragraph(item.name, styles["cell_left"])
        else:
            # 부가서비스: 상위 항목에 딸린 하위 서비스임을 들여쓰기+연결선으로 표시
            addon_rows.append(len(rows))
            name = Paragraph("　└ " + item.name, styles["cell_addon"])
        rows.append([
            name,
            Paragraph(item.spec, styles["cell"]),
            Paragraph(str(item.qty), styles["cell"]),
            Paragraph(_num(item.unit_price), styles["cell"]),
            Paragraph(_num(item.supply), styles["cell"]),
            Paragraph(_num(item.vat), styles["cell"]),
            Paragraph("", styles["cell"]),
        ])

    # 합계 행: 공급가액·세액을 나누지 않고 부가세 포함 합계 한 값으로 표시
    rows.append([
        Paragraph("합 계", styles["cell"]),
        "", "", "",
        Paragraph(_num(quote.grand_total), styles["cell"]),
        "",
        Paragraph("", styles["cell"]),
    ])
    total_row = len(rows) - 1
    span_cmds.append(("SPAN", (0, total_row), (3, total_row)))
    span_cmds.append(("SPAN", (4, total_row), (5, total_row)))

    col_widths = [58 * mm, 20 * mm, 12 * mm, 24 * mm, 24 * mm, 20 * mm, 16 * mm]
    t = Table(rows, colWidths=col_widths, repeatRows=1)
    style = [
        ("FONTNAME", (0, 0), (-1, -1), _FONT),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
        ("BACKGROUND", (0, total_row), (-1, total_row), colors.HexColor("#eef2ee")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for r in addon_rows:
        style.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#fafafa")))
    style.extend(span_cmds)
    t.setStyle(TableStyle(style))
    return t
