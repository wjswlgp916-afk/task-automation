"""독점 공급 확인서 렌더러 (HWP + PDF).

'성균관대학교 교육과미래연구소'가 K-NSSE/UICA 를 독점 운영한다는 확인서.
본문 한 문단 안에 K-NSSE·UICA 관련 문구가 섞여 있고, 등급코드에 담긴
도구 구성(K 단독/U 단독/K+U 결합)에 따라 본문이 통째로 달라진다.

원본 양식은 K+U 결합 버전이며, 본문 문단 안의 UICA 관련 문구 5곳에
안내 메모(개별 확인용)가 걸려 있다. K 단독일 때는 이 5곳을 포함해 본문이
K-NSSE 만의 문구로 바뀌어야 하고(사용자가 준 배재대학교 참고본과 동일),
U 단독일 때도 마찬가지로 UICA 만의 문구로 바뀐다(포항공과대학교 참고본과
동일). 두 경우 모두, 그 5개 메모는 본문과 함께 사라져야 한다(제거 안 하면
앵커 없는 메모만 남아 "파일 손상"이 난다 — 계약서에서 이미 겪은 문제).

건명(자문계약명) 교체 규칙은 다른 서류와 동일: K 단독·결합은 기본값
유지, U 단독은 "대학 혁신역량 진단 및 분석"으로 변경 — engine.subject_override
공유. 계약명 자리에는 별도 안내 메모(id=6, "UICA만 시행할 경우 -> 계약명
변경")가 있지만, 이건 값이 들어있는 문단 안쪽만 부분 치환(replace_literal)
하므로 메모 마커를 건드리지 않아 그대로 둔다.

발급일자는 quote.issue_date 로 채운다(확인서를 실제로 발급하는 날 —
계약서의 '계약체결일'과 달리 대학이 나중에 기입하는 자리가 아니다).
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import List, Optional

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

from . import cfbf, pdf_common
from .engine import Quote, subject_override
from .hwp_writer import (
    PARA_HEADER,
    PARA_TEXT,
    PARA_CHAR_SHAPE,
    PARA_LINE_SEG,
    CTRL_HEADER,
    MEMO_LIST,
    Record,
    parse_records,
    serialize_records,
    replace_literal_everywhere,
    set_plain_text,
    text_of,
)

_INSTITUTE = "성균관대학교 교육과미래연구소"
_MEMO_CTRL_ID = b"knu%"

# 원본 양식의 정확한 placeholder 리터럴
_SUBJECT_DEFAULT = "학부교육의 질과 성과 진단 및 분석 "   # 계약명 자리(메모로 감싸여 있음, 끝 공백 포함)
_UNIV = "OO대학교"
_DATE_PLACEHOLDER = "2026. . ."

# 본문(K+U 결합 -> 단일 도구) 완성 문구. 실제 참고 문서(배재대학교/포항공과
# 대학교) 를 그대로 옮긴 것 — 대학명 자리만 채운다.
_BODY_K_ONLY = (
    "성균관대학교 교육과미래연구소에서 수행하는 ‘학부교육의 질과 성과 진단 및 분석’"
    "자문은 학부교육 실태조사(K-NSSE)와를 진단도구로 사용하고 있습니다. 학부교육 "
    "실태조사(K-NSSE)는 성균관대학교 교육과미래연구소에서 독점으로 운영하며, 본 "
    "연구소에서 결과에 대한 진단과 분석을 수행하고 있습니다. 학부교육 실태조사"
    "(K-NSSE)는 대학생의 학습과정 및 대학생활 경험을 진단하는 조사이며, 이를 통해 "
    "학부교육의 질과 성과를 진단할 수 있습니다. 이에 {univ}의 학부교육의 질과 성과 "
    "등에 대한 자문을 제공하기 위해서는 본 기관에서 운영 중인 학부교육 실태조사"
    "(K-NSSE)와 를 진단도구로 사용해야 하는바 수의계약의 방법으로 자문 계약을 "
    "체결하고자 합니다."
)
_BODY_U_ONLY = (
    "성균관대학교 교육과미래연구소에서 수행하는 ‘대학 혁신역량 진단 및 분석’자문은 "
    "대학 혁신역량 진단조사(UICA)를 진단도구로 사용하고 있습니다. 대학 혁신역량 "
    "진단조사(UICA)는 성균관대학교 교육과미래연구소에서 독점으로 운영하며, 본 "
    "연구소에서 결과에 대한 진단과 분석을 수행하고 있습니다. 대학 혁신역량 진단조사"
    "(UICA)는 대학이 조직 차원에서 얼마나 혁신적으로 운영되고 있는지, 변화와 혁신을 "
    "위한 문화와 풍토를 가지고 있는지, 대학 구성원은 얼마나 혁신적으로 업무를 "
    "수행하고 있는지를 진단하고 분석합니다. 이에 {univ}의 혁신 역량 등에 대한 자문을 "
    "제공하기 위해서는 본 기관에서 운영 중인 대학 혁신역량 진단조사(UICA)를 진단도구로 "
    "사용해야 하는바 수의계약의 방법으로 자문 계약을 체결하고자 합니다."
)

# 본문 문단 안에서 UICA 관련 문구에 걸린 메모 5개의 ID(고정, 템플릿 구조상 불변)
_BODY_MEMO_IDS = {1, 2, 3, 4, 5}


class ExclusiveSupplyError(RuntimeError):
    pass


def _default_template() -> Path:
    return Path(__file__).parent / "templates" / "exclusive_supply_template.hwp"


def _fmt_date(quote: Quote) -> str:
    d = quote.issue_date
    return f"{d.year}. {d.month}. {d.day}."


def _find_para(records: List[Record], needle: str):
    """needle 이 들어있는 PARA_TEXT 와 그 PARA_HEADER 인덱스를 찾는다."""
    for i, r in enumerate(records):
        if r.tag == PARA_TEXT and needle in text_of(r):
            for j in range(i - 1, -1, -1):
                if records[j].tag == PARA_HEADER:
                    return j, i
                if records[j].tag == PARA_TEXT:
                    break
    raise ExclusiveSupplyError(f"양식에서 '{needle}' 문단을 찾지 못했습니다.")


def _drop_memos(records: List[Record], ids: set) -> None:
    """지정한 ID 의 메모(CTRL_HEADER + MEMO_LIST)만 정확히 제거한다.

    인라인 마커는 문단을 순수 텍스트로 다시 쓰면서 이미 사라졌다는 전제.
    """
    records[:] = [
        r for r in records
        if not (r.tag == CTRL_HEADER and r.payload[:4] == _MEMO_CTRL_ID
                and struct.unpack("<I", r.payload[-4:])[0] in ids)
    ]
    out: List[Record] = []
    i, n = 0, len(records)
    while i < n:
        r = records[i]
        if r.tag == MEMO_LIST and struct.unpack("<I", r.payload[0:4])[0] in ids:
            base = r.level
            j = i + 1
            while j < n and records[j].tag != MEMO_LIST and records[j].level >= base:
                j += 1
            i = j
            continue
        out.append(r)
        i += 1
    records[:] = out


def _rewrite_body_paragraph(records: List[Record], hdr_idx: int, txt_idx: int,
                            new_text: str) -> None:
    """본문 문단을 완전히 새 텍스트로 바꾼다(메모 5개가 함께 사라지는 자리).

    문단이 순수 텍스트가 되므로:
      * PARA_HEADER 의 컨트롤 마스크([4:8])를 지운다(더 이상 필드 없음).
      * 글자모양(CHAR_SHAPE)은 원래도 문단 전체에 단일 항목(위치 0)뿐이라
        손댈 필요 없다(길이만 바뀌어도 위치 0 은 항상 유효).
      * 줄나눔 정보(LINE_SEG)는 문단 전체가 한 줄인 것처럼 1개 항목으로
        축소하고, 헤더의 줄 개수 필드([16:18])도 1로 맞춘다 — 실제 줄바꿈은
        한글이 편집기에서 다시 계산한다(표 행 높이와 같은 이치).
    """
    hdr, txt = records[hdr_idx], records[txt_idx]
    set_plain_text(hdr, txt, new_text)
    hdr.payload = hdr.payload[0:4] + b"\x00\x00\x00\x00" + hdr.payload[8:]

    ls = None
    for j in range(txt_idx + 1, len(records)):
        if records[j].tag == PARA_LINE_SEG:
            ls = records[j]
            break
        if records[j].tag in (PARA_HEADER, PARA_TEXT):
            break
    if ls is not None and len(ls.payload) >= 36:
        first_seg = bytearray(ls.payload[0:36])
        struct.pack_into("<i", first_seg, 0, 0)   # chpos = 0
        ls.payload = bytes(first_seg)
        p = bytearray(hdr.payload)
        struct.pack_into("<H", p, 16, 1)          # line_seg_count = 1
        hdr.payload = bytes(p)


def render_hwp(quote: Quote, out_path: str | Path, template: Optional[Path] = None,
               extra: Optional[dict] = None) -> Path:
    """독점 공급 확인서를 HWP 파일로 저장하고 경로를 반환한다."""
    tools = quote.tools
    if tools not in ({"K"}, {"U"}, {"K", "U"}):
        raise ExclusiveSupplyError(f"지원하지 않는 도구 구성입니다: {tools}")

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

    # 1) 계약명(자문계약명) — 문단 안 부분 치환이라 메모(id=6)는 그대로 둔다
    subject = subject_override(quote)
    if subject:
        if replace_literal_everywhere(records, _SUBJECT_DEFAULT, f"{subject} ") != 1:
            raise ExclusiveSupplyError("양식에서 자문계약명 자리를 찾지 못했습니다.")

    # 2) 본문 문단: 단일 도구면 통째로 다시 쓰고 UICA 관련 메모 5개를 제거,
    #    K+U 결합이면 손대지 않는다(대학명은 3번 단계의 전역 치환으로 채워짐).
    if tools != {"K", "U"}:
        body = (_BODY_K_ONLY if tools == {"K"} else _BODY_U_ONLY).format(univ=quote.university)
        hdr_idx, txt_idx = _find_para(records, "성균관대학교 교육과미래연구소에서 수행하는")
        _rewrite_body_paragraph(records, hdr_idx, txt_idx, body)
        _drop_memos(records, _BODY_MEMO_IDS)

    # 3) 대학명 (자문요청기관 줄 + K+U 결합일 때 본문 안 placeholder)
    if replace_literal_everywhere(records, _UNIV, quote.university) < 1:
        raise ExclusiveSupplyError("양식에서 자문요청기관(대학명) 자리를 찾지 못했습니다.")

    # 4) 발급일자
    if replace_literal_everywhere(records, _DATE_PLACEHOLDER, _fmt_date(quote)) != 1:
        raise ExclusiveSupplyError("양식에서 발급일자 자리를 찾지 못했습니다.")

    body_bytes = serialize_records(records)
    if compressed:
        co = zlib.compressobj(9, zlib.DEFLATED, -15)
        packed = co.compress(body_bytes) + co.flush()
    else:
        packed = body_bytes
    new_streams = [
        (parts, packed if tuple(parts) == ("BodyText", "Section0") else d)
        for parts, d in streams
    ]
    cfbf.write_cfbf(str(out_path), new_streams)
    return out_path


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
_STAMP_PATH = Path(__file__).parent / "templates" / "stamp_institute.png"


def render_pdf(quote: Quote, out_path: str | Path, extra: Optional[dict] = None) -> Path:
    """독점 공급 확인서를 PDF 파일로 저장하고 경로를 반환한다."""
    tools = quote.tools
    if tools not in ({"K"}, {"U"}, {"K", "U"}):
        raise ExclusiveSupplyError(f"지원하지 않는 도구 구성입니다: {tools}")

    bold = pdf_common.register_fonts()
    font = pdf_common.FONT
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    st = {
        "title": ParagraphStyle("title", fontName=bold, fontSize=22, alignment=TA_CENTER, leading=28),
        "item": ParagraphStyle("item", fontName=font, fontSize=12.5, alignment=TA_LEFT, leading=20),
        "body": ParagraphStyle("body", fontName=font, fontSize=12, alignment=TA_JUSTIFY, leading=21),
        "center": ParagraphStyle("center", fontName=font, fontSize=12.5, alignment=TA_CENTER, leading=18),
        "org": ParagraphStyle("org", fontName=bold, fontSize=15, alignment=TA_CENTER, leading=22),
    }

    left = right = 24 * mm
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=left, rightMargin=right, topMargin=25 * mm, bottomMargin=20 * mm,
        title=f"독점공급확인서_{quote.university}",
    )

    subject = subject_override(quote) or _SUBJECT_DEFAULT.strip()
    if tools == {"K"}:
        body = _BODY_K_ONLY.format(univ=quote.university)
    elif tools == {"U"}:
        body = _BODY_U_ONLY.format(univ=quote.university)
    else:
        body = (
            "성균관대학교 교육과미래연구소에서 수행하는 ‘학부교육의 질과 성과 진단 및 분석’"
            "자문은 학부교육 실태조사(K-NSSE)와 대학 혁신역량 진단조사(UICA)를 진단도구로 "
            "사용하고 있습니다. 학부교육 실태조사(K-NSSE)와 대학 혁신역량 진단조사(UICA)는 "
            "성균관대학교 교육과미래연구소에서 독점으로 운영하며, 본 연구소에서 결과에 대한 "
            "진단과 분석을 수행하고 있습니다. 학부교육 실태조사(K-NSSE)는 대학생의 학습과정 "
            "및 대학생활 경험을 진단하는 조사이며, 이를 통해 학부교육의 질과 성과를 진단할 수 "
            "있습니다. 대학 혁신역량 진단조사(UICA)는 대학이 조직 차원에서 얼마나 혁신적으로 "
            "운영되고 있는지, 변화와 혁신을 위한 문화와 풍토를 가지고 있는지, 대학 구성원은 "
            "얼마나 혁신적으로 업무를 수행하고 있는지를 진단하고 분석합니다. 이에 "
            f"{quote.university}의 학부교육의 질과 성과, 혁신 역량 등에 대한 자문을 제공하기 "
            "위해서는 본 기관에서 운영 중인 학부교육 실태조사(K-NSSE)와 대학 혁신역량 진단조사"
            "(UICA)를 진단도구로 사용해야 하는바 수의계약의 방법으로 자문 계약을 체결하고자 "
            "합니다."
        )

    story = []
    story.append(Paragraph("독점 공급 확인서", st["title"]))
    story.append(Spacer(1, 14 * mm))
    story.append(Paragraph(f"1. 자문수행기관 : {_INSTITUTE}", st["item"]))
    story.append(Paragraph(f"2. 자문요청기관 : {quote.university}", st["item"]))
    story.append(Paragraph(f"3. 자문계약명 : {subject}", st["item"]))
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(body, st["body"]))
    story.append(Spacer(1, 14 * mm))
    story.append(Paragraph(_fmt_date(quote), st["center"]))
    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(_INSTITUTE, st["org"]))

    if _STAMP_PATH.exists():
        from reportlab.pdfbase.pdfmetrics import stringWidth
        text_w = stringWidth(_INSTITUTE, bold, 15)
        center_x = (doc.width - text_w) / 2 + text_w * 0.72
        size = 20 * mm
        story.append(pdf_common.StampOverlay(str(_STAMP_PATH), size=size,
                                             x=center_x - size / 2, overlap=15 * mm))

    doc.build(story)
    return out_path
