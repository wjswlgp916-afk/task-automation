"""대금청구서 렌더러.

견적서·거래명세서와 달리 품목 테이블이 없는 짧은 1페이지 공문서다.
청구금액·발급일자·대학명(수신처)만 바뀌고, 계좌정보·발급자 정보(성균관대
산학협력단)는 고정값이라 손대지 않는다.

청구금액 등 자리가 라벨과 한 문장에 섞여 있어("청구금액 : 금 O백O십O만원
(\\ 0,000,000 )"), 문단 전체가 아니라 **문장 안 일부만** 바꿔야 한다.
이때는 hwp_writer.replace_literal_everywhere() 를 쓴다.

건명(사업명)은 원본 기본값이 K-NSSE 문구인데, 청구 대상이 UICA 단독이면
"대학 혁신역량 진단 및 분석"으로 바뀌어야 하고, K 단독이거나 두 도구가
함께면 원본 그대로 둔다 (사용자 확정 규칙).
"""

from __future__ import annotations

import re
import struct
import zlib
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, Flowable,
)

# 흰 배경을 투명 처리해 붉은 인영만 남기는 색상 키 마스크 (글자 위에 겹칠 때)
_STAMP_WHITE_MASK = [230, 255, 230, 255, 230, 255]


class _StampOverlay(Flowable):
    """도장 이미지를 바로 위 줄(대표자 '(인)')에 겹쳐 찍는다.

    reportlab flowable 은 겹침을 직접 지원하지 않으므로, 실제 도장보다 낮은
    높이만 차지하고 이미지를 위쪽(이전 줄)까지 올려 그린다. 흰 배경은
    색상 키 마스크로 투명 처리해 글자가 비쳐 보이도록 한다.
    """

    def __init__(self, path: str, size: float, x: float, overlap: float):
        super().__init__()
        self.path = path
        self.size = size          # 도장 한 변 길이
        self.x = x                # 프레임 왼쪽 기준 x
        self.overlap = overlap    # 위 줄로 겹쳐 올라갈 높이

    def wrap(self, availWidth, availHeight):
        return (availWidth, max(0.0, self.size - self.overlap))

    def draw(self):
        # 로컬 원점(0,0)=박스 좌하단. y=0 에서 size 높이로 그리면
        # 위쪽 overlap 만큼 이전 줄(대표자)에 겹쳐진다.
        self.canv.drawImage(
            self.path, self.x, 0, width=self.size, height=self.size,
            mask=_STAMP_WHITE_MASK, preserveAspectRatio=True,
        )

from . import cfbf, pdf_common
from .catalog import CATALOG, COMPANY
from .engine import Quote
from .hwp_writer import (
    parse_records,
    serialize_records,
    replace_literal_everywhere,
)
from .korean_num import number_to_korean_plain

# 원본 양식의 정확한 placeholder 리터럴
_DATE_PLACEHOLDER = "2026. 00. 00."
_UNIV_PLACEHOLDER = "OO대학교"
_AMOUNT_PLACEHOLDER = "금 O백O십O만원 (\\ 0,000,000 )"
_SUBJECT_PLACEHOLDER = "학부교육의 질과 성과 진단 및 분석"   # 원본 기본 건명 (K-NSSE)


class InvoiceError(RuntimeError):
    pass


def _default_template() -> Path:
    return Path(__file__).parent / "templates" / "invoice_template.hwp"


def _short_name(tool_code: str) -> str:
    """카탈로그의 정식 품명에서 '(K-NSSE)'/'(UICA)' 같은 괄호 표기를 뗀 건명."""
    full = CATALOG[tool_code].product_name
    return re.sub(r"\([^)]*\)\s*$", "", full).strip()


def _subject_override(quote: Quote) -> Optional[str]:
    """건명을 바꿔야 하면 새 건명을, 기본값을 유지하면 None 을 반환한다.

    규칙(사용자 확정): K 단독 또는 K+U 결합이면 원본 기본값(K 문구) 유지,
    U 단독이면 "대학 혁신역량 진단 및 분석"으로 교체.
    """
    tools = {code.split("_", 1)[0] for code in quote.source_codes}
    if tools == {"U"}:
        return _short_name("U")
    return None


def _amount_text(quote: Quote) -> str:
    """대금청구서 고유 표기 스타일: '금 삼백삼십만원 (\\ 3,300,000 )'."""
    kor = number_to_korean_plain(quote.grand_total)
    return f"금 {kor}원 (\\ {quote.grand_total:,} )"


def _date_text(quote: Quote) -> str:
    d = quote.issue_date
    return f"{d.year}. {d.month:02d}. {d.day:02d}."


def render_hwp(quote: Quote, out_path: str | Path, template: Optional[Path] = None) -> Path:
    """대금청구서를 HWP 파일로 저장하고 경로를 반환한다."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tpl = Path(template) if template else _default_template()

    streams = cfbf.read_streams(str(tpl))
    sm = {tuple(p): d for p, d in streams}
    fh = sm[("FileHeader",)]
    (flags,) = struct.unpack("<I", fh[36:40])
    compressed = bool(flags & 1)
    raw = sm[("BodyText", "Section0")]
    data = zlib.decompress(raw, -15) if compressed else raw
    records = parse_records(data)

    if replace_literal_everywhere(records, _DATE_PLACEHOLDER, _date_text(quote)) != 1:
        raise InvoiceError("템플릿에서 발급일자 자리를 찾지 못했습니다.")
    if replace_literal_everywhere(records, _UNIV_PLACEHOLDER, quote.university) != 1:
        raise InvoiceError("템플릿에서 대학명 자리를 찾지 못했습니다.")
    if replace_literal_everywhere(records, _AMOUNT_PLACEHOLDER, _amount_text(quote)) != 1:
        raise InvoiceError("템플릿에서 청구금액 자리를 찾지 못했습니다.")

    subject = _subject_override(quote)
    if subject:
        if replace_literal_everywhere(records, _SUBJECT_PLACEHOLDER, subject) != 1:
            raise InvoiceError("템플릿에서 건명 자리를 찾지 못했습니다.")

    body = serialize_records(records)

    if compressed:
        co = zlib.compressobj(9, zlib.DEFLATED, -15)
        packed = co.compress(body) + co.flush()
    else:
        packed = body

    new_streams = []
    for parts, d in streams:
        if tuple(parts) == ("BodyText", "Section0"):
            new_streams.append((parts, packed))
        else:
            new_streams.append((parts, d))

    cfbf.write_cfbf(str(out_path), new_streams)
    return out_path


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
def render_pdf(quote: Quote, out_path: str | Path) -> Path:
    """대금청구서를 PDF 파일로 저장하고 경로를 반환한다."""
    bold = pdf_common.register_fonts()
    font = pdf_common.FONT
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    styles = {
        "title": ParagraphStyle("title", fontName=bold, fontSize=24, alignment=TA_CENTER, leading=30),
        "normal": ParagraphStyle("normal", fontName=font, fontSize=12, alignment=TA_LEFT, leading=18),
        "bold_line": ParagraphStyle("bold_line", fontName=bold, fontSize=13, alignment=TA_LEFT, leading=19),
        "greeting": ParagraphStyle("greeting", fontName=bold, fontSize=12.5, alignment=TA_CENTER, leading=18),
        "acc_label": ParagraphStyle("acc_label", fontName=bold, fontSize=11, alignment=TA_CENTER, leading=15),
        "acc_value": ParagraphStyle("acc_value", fontName=font, fontSize=11, alignment=TA_CENTER, leading=15),
        "iss_label": ParagraphStyle("iss_label", fontName=bold, fontSize=11.5, alignment=TA_LEFT, leading=17),
        "iss_value": ParagraphStyle("iss_value", fontName=font, fontSize=11.5, alignment=TA_LEFT, leading=17),
        "center": ParagraphStyle("center", fontName=font, fontSize=12, alignment=TA_CENTER, leading=18),
    }

    # 페이지 폭을 최대한 활용하도록 여백을 줄이고 표를 프레임 전체 폭으로 편다.
    left = right = 20 * mm
    content_w = A4[0] - left - right      # ≈ 170mm
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=left, rightMargin=right,
        topMargin=22 * mm, bottomMargin=20 * mm,
        title=f"대금청구서_{quote.university}",
    )

    subject = _subject_override(quote) or _SUBJECT_PLACEHOLDER
    story = []
    story.append(Paragraph("대 금 청 구 서", styles["title"]))
    story.append(Spacer(1, 12 * mm))

    story.append(Paragraph(f"건 명 : {subject}", styles["normal"]))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(f"청구금액 : {_amount_text(quote)}", styles["bold_line"]))
    story.append(Spacer(1, 9 * mm))

    story.append(Paragraph("위 금액을 청구하니 아래 계좌로 송금하여 주시기 바랍니다.", styles["greeting"]))
    story.append(Spacer(1, 9 * mm))

    story.append(Paragraph("(예금계좌)", styles["normal"]))
    story.append(Spacer(1, 2 * mm))
    # 계좌 표를 프레임 전체 폭으로 (라벨 열 고정, 값 열이 나머지를 모두 차지)
    acc_label_w = 40 * mm
    account = Table(
        [
            [Paragraph("거 래 은 행", styles["acc_label"]), Paragraph("우리은행", styles["acc_value"])],
            [Paragraph("계 좌 번 호", styles["acc_label"]), Paragraph("1005-001-161242", styles["acc_value"])],
            [Paragraph("통 장 명 의", styles["acc_label"]), Paragraph("성균관대학교 산학협력단", styles["acc_value"])],
        ],
        colWidths=[acc_label_w, content_w - acc_label_w], rowHeights=9 * mm,
    )
    account.hAlign = "LEFT"
    account.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1.0, colors.black),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f2f2f2")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(account)
    story.append(Spacer(1, 12 * mm))

    story.append(Paragraph(_date_text(quote), styles["center"]))
    story.append(Spacer(1, 6 * mm))

    # 발급자 정보: 라벨 열을 오른쪽 정렬(콜론 맞춤)해 원본 양식처럼 보이게.
    iss_label_w = 40 * mm
    label_style = ParagraphStyle("iss_label_r", parent=styles["iss_label"], alignment=2)  # RIGHT
    issuer = Table(
        [
            [Paragraph("신 청 자 주 소 :", label_style), Paragraph(COMPANY.address, styles["iss_value"])],
            [Paragraph("상 호 :", label_style), Paragraph(COMPANY.name, styles["iss_value"])],
            [Paragraph("대 표 자 :", label_style), Paragraph(COMPANY.ceo, styles["iss_value"])],
        ],
        colWidths=[iss_label_w, content_w - iss_label_w], rowHeights=8.6 * mm,
    )
    issuer.hAlign = "LEFT"
    issuer.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("RIGHTPADDING", (0, 0), (0, -1), 6),
        ("LEFTPADDING", (1, 0), (1, -1), 4),
    ]))
    story.append(issuer)

    # 도장: 대표자 값("구자춘 (인)")의 '(인)' 정중앙 위에 겹쳐 찍는다.
    if pdf_common.STAMP_PATH.exists():
        from reportlab.pdfbase.pdfmetrics import stringWidth
        ceo = COMPANY.ceo                     # 예: "구자춘 (인)"
        fs = 11.5
        brace_at = ceo.find("(")
        prefix = ceo[:brace_at] if brace_at >= 0 else ceo
        brace = ceo[brace_at:] if brace_at >= 0 else ""
        value_start = iss_label_w + 4 * mm    # 값 열 텍스트 시작 x
        brace_center = (value_start
                        + stringWidth(prefix, font, fs)
                        + stringWidth(brace, font, fs) / 2)
        size = 18 * mm
        story.append(_StampOverlay(str(pdf_common.STAMP_PATH), size=size,
                                   x=brace_center - size / 2, overlap=13 * mm))

    story.append(Spacer(1, 16 * mm))
    story.append(Paragraph(f"{quote.university} 총장 귀하", styles["normal"]))

    doc.build(story)
    return out_path
