"""독점 공급 확인서 렌더러 (HWP + PDF).

'성균관대학교 교육과미래연구소'가 K-NSSE/UICA 를 독점 운영한다는 확인서.
본문 한 문단 안에 K-NSSE·UICA 관련 문구가 섞여 있고, 등급코드에 담긴
도구 구성(K 단독/U 단독/K+U 결합)에 따라 본문이 통째로 달라진다.

원본 양식은 K+U 결합 버전이며, 본문 문단 안의 UICA 관련 문구 5곳과 계약명
문단에 안내 메모(6개, 모두 실무용 — 대학에 보여줄 필요 없음)가 걸려 있다.
사용자 확정 규칙: 이 메모는 전부 삭제한다. K 단독일 때는 본문이 K-NSSE 만의
문구로 바뀌고(배재대학교 참고본과 동일), U 단독일 때는 UICA 만의 문구로
바뀐다(포항공과대학교 참고본과 동일).

건명(자문계약명) 교체 규칙은 다른 서류와 동일: K 단독·결합은 기본값 유지,
U 단독은 "대학 혁신역량 진단 및 분석"으로 변경 — engine.subject_override 공유.

구현 메모(중요, 문서 보안설정 [높음] 대응 — 최종 결론):
이 문제의 진짜 원인은 메모(CTRL_HEADER)와는 전혀 관계가 없었다. 실제
한글에서 메모를 직접 삭제해 저장한 파일과 원본을 바이트 단위로 비교해
본 결과, 한글 자신도 메모를 지울 때 hwp_writer.strip_memo_controls() 와
구조적으로 거의 동일하게 CTRL_HEADER/MEMO_LIST 를 통째로 제거한다는
것을 확인했다("CTRL_HEADER 를 지우면 높음 보안에서 막힌다"는 이전
결론은 근거가 부족했던 것으로 판명).

여러 단계의 실측(사용자가 실제 한글 [높음] 보안설정에서 직접 테스트)으로
좁혀본 결과, 진짜 원인은 **문단 텍스트 길이를 바꿀 때 PARA_LINE_SEG(줄
나눔 캐시)를 그대로 두는 것**이었다 — 단, 그 문단이 원래 **2줄 이상**으로
나뉘어 있던 경우에만 문제가 된다(본문 문단이 딱 이 경우: 15개 세그먼트).
반대로 대학명·발급일자 같이 원래 **1줄**뿐인 짧은 문단은 길이가 달라져도
낡은 LINE_SEG 를 그대로 둬도 문제없이 열린다(실측 확인). 전체를
set_plain_text() 로 다시 쓰든, replace_literal_everywhere() 로 부분만
잘라 편집하든 결과는 같았다 — "다시 쓰는 방식"이 문제가 아니라 순전히
"낡은 LINE_SEG 를 그대로 둔 채 2줄 이상 문단의 글자 수를 바꾸는 것"이
문제였다.

그래서 본문 문단을 다시 쓴 뒤에는 반드시 hwp_writer.clear_stale_line_seg()
로 낡은 LINE_SEG 를 지우고 개수를 0으로 만든다(세그먼트가 2개 이상일
때만 손을 댐 — 원래 1개면 그대로 둬도 안전하므로 손대지 않는다). 이렇게
하면 한글이 캐시가 없다고 보고 줄바꿈을 새로 계산한다. (참고: 세그먼트
개수를 억지로 1개라고 거짓 선언하면 한글이 그 선언을 그대로 믿어 모든
글자를 한 줄에 겹쳐 그리는 손상이 나므로 — 이건 "개수 0(캐시 없음)"과는
전혀 다른, 이미 겪어서 확인한 별개의 문제다.)

⚠ 놓치기 쉬운 부분(K+U 결합에서도 실측으로 다시 겪음): strip_memo_controls()
자체가 본문 문단 안 인라인 필드 마커 5개를 지우면서 이미 그 문단의 글자
수를 바꾼다 — K/U 단독이면 뒤이어 본문을 통째로 다시 쓰면서 자연스럽게
LINE_SEG 도 함께 정리되지만, K+U 결합은 본문 문구를 더 이상 건드리지
않으므로 이 단계에서 정리해주지 않으면 낡은 LINE_SEG 가 그대로 남는다.
그래서 render_hwp() 는 strip_memo_controls() 직후, 도구 구성과 무관하게
본문 문단의 clear_stale_line_seg() 를 항상 한 번 호출한다.

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
    Record,
    clear_stale_line_seg,
    parse_records,
    serialize_records,
    replace_literal_everywhere,
    set_plain_text,
    strip_memo_controls,
    text_of,
)

_INSTITUTE = "성균관대학교 교육과미래연구소"

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


def _rewrite_paragraph(records: List[Record], hdr_idx: int, txt_idx: int,
                       new_text: str) -> None:
    """문단을 완전히 새 텍스트로 바꾼다.

    글자 수가 바뀌므로, 이 문단이 원래 2줄 이상으로 나뉘어 있었다면 낡은
    LINE_SEG(줄 나눔 캐시)를 지워야 한다 — 그대로 두면 문서 보안설정
    [높음]에서 파일이 열리지 않는다(실측 확인, 모듈 docstring 참고).
    clear_stale_line_seg() 는 원래 1줄뿐이던 문단은 손대지 않는다(그
    경우는 그대로 둬도 안전함)."""
    set_plain_text(records[hdr_idx], records[txt_idx], new_text)
    clear_stale_line_seg(records, hdr_idx)


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

    # 0) 실무용 메모 6개(본문 UICA 문구 5개 + 계약명 자리 1개) 전부 제거.
    #    감싸여 있던 문단은 순수 텍스트가 되어 보이는 글자는 그대로 남는다.
    strip_memo_controls(records)

    # 0-1) 본문 문단은 메모 마커가 5개 제거되면서 글자 수가 바뀌었는데,
    #    원래 15줄(LINE_SEG 15개)로 나뉜 문단이라 그 캐시를 그대로 두면
    #    문서 보안설정 [높음]에서 파일이 열리지 않는다(실측 확인) — K/U
    #    단독이면 아래 2)번에서 문단을 통째로 다시 쓰며 다시 한 번 정리
    #    되지만, K+U 결합은 본문을 더 이상 건드리지 않으므로 여기서 반드시
    #    정리해야 한다(도구 구성과 무관하게 항상 실행).
    hdr_idx, _ = _find_para(records, "성균관대학교 교육과미래연구소에서 수행하는")
    clear_stale_line_seg(records, hdr_idx)

    # 1) 계약명(자문계약명)
    subject = subject_override(quote)
    if subject:
        if replace_literal_everywhere(records, _SUBJECT_DEFAULT, f"{subject} ") != 1:
            raise ExclusiveSupplyError("양식에서 자문계약명 자리를 찾지 못했습니다.")

    # 2) 본문 문단: 단일 도구면 통째로 다시 쓴다(K+U 결합이면 손대지 않고,
    #    대학명은 3번 단계의 전역 치환으로 채워짐).
    if tools != {"K", "U"}:
        body = (_BODY_K_ONLY if tools == {"K"} else _BODY_U_ONLY).format(univ=quote.university)
        hdr_idx, txt_idx = _find_para(records, "성균관대학교 교육과미래연구소에서 수행하는")
        _rewrite_paragraph(records, hdr_idx, txt_idx, body)

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
