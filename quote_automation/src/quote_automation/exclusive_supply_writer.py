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

구현 메모(중요, 문서 보안설정 [높음] 대응): 실측으로 확인한 바, 한글 메모
(코멘트)의 앵커(CTRL_HEADER, id="knu%")를 조금이라도 건드리면(제거·이동 등)
문서 보안설정이 [높음]일 때 파일이 열리지 않는다. 반대로 앵커는 완전히
그대로 두고, 메모 내용(MEMO_LIST 안 문단)이나 메모가 감싸는 본문 구간의
"내용"만 공백 1개로 비우는 것은 높음 보안에서도 정상적으로 열린다(단,
완전히 빈 문자열로 만들면 그 자체로 파일이 손상된다 — 빈 문단이 유효하지
않은 것으로 보임). 따라서:
  * strip_memo_controls_safe() 로 메모 내용(MEMO_LIST)만 공백으로 비우고,
    앵커(CTRL_HEADER)는 절대 지우거나 옮기지 않는다.
  * K 단독 본문에서 UICA 관련 메모 구간(5곳)은 그 안의 텍스트를 공백
    1개로 비우고, 그 주변의 순수 텍스트만 안전하게 다듬어 배재대학교
    참고본과 최대한 같은 문구가 되도록 한다(메모로 감싸인 구간 자체를
    완전히 삭제하면 크래시/손상이 나므로 공백 1개가 최선).
  * U 단독 본문에서는 반대로 UICA 메모 구간은 원본 그대로 두고, K-NSSE
    관련 순수 텍스트(메모로 감싸여 있지 않음)만 안전하게 지운다 — 이
    경우는 메모를 전혀 건드리지 않으므로 포항공과대학교 참고본과 완전히
    같은 문구를 안전하게 만들 수 있다.
  * 문단 전체를 set_plain_text() 로 통째로 다시 쓰는 방식은 쓰지 않는다
    (메모 앵커가 가리키는 인라인 필드 마커까지 사라져 버려 위험하다).

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
    Record,
    parse_records,
    serialize_records,
    replace_literal_everywhere,
    strip_memo_controls_safe,
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

# 본문 문단 안, UICA 관련 메모가 감싸고 있는 정확한 원문 구간(공백 1개로
# 비울 대상). 짧은 문구가 긴 문장의 접두어이므로 반드시 문장 -> 짧은 문구
# 순서로 치환해야 한다(짧은 문구를 먼저 지우면 문장의 앞부분만 잘려나간다).
_WRAP_UICA_SENTENCE = (
    "대학 혁신역량 진단조사(UICA)는 대학이 조직 차원에서 얼마나 혁신적으로 "
    "운영되고 있는지, 변화와 혁신을 위한 문화와 풍토를 가지고 있는지, 대학 "
    "구성원은 얼마나 혁신적으로 업무를 수행하고 있는지를 진단하고 분석합니다."
)
_WRAP_UICA_SHORT = "대학 혁신역량 진단조사(UICA)"
_WRAP_INNOVATION = "혁신 역량"


class ExclusiveSupplyError(RuntimeError):
    pass


def _default_template() -> Path:
    return Path(__file__).parent / "templates" / "exclusive_supply_template.hwp"


def _fmt_date(quote: Quote) -> str:
    d = quote.issue_date
    return f"{d.year}. {d.month}. {d.day}."


def _replace_all(records: List[Record], old: str, new: str) -> int:
    """``old`` 가 같은 문단 안에 여러 번 나와도 전부 치환한다.

    replace_literal_everywhere() 는 문단(PARA_TEXT) 하나당 첫 매치 1회만
    바꾸므로(한 문단 안에 ``old`` 가 여러 번 나오면 나머지는 놓친다), 더
    찾지 못할 때까지 반복 호출해 누적한다. ``new`` 가 ``old`` 를 포함하면
    무한 루프가 되므로 호출부에서 그런 조합은 쓰지 않는다.
    """
    total = 0
    while True:
        n = replace_literal_everywhere(records, old, new)
        if n == 0:
            return total
        total += n


def _apply_same_length_edits(records: List[Record], edits, what: str) -> None:
    """(old, 보이는 새 글자, 예상 치환 횟수) 목록을 순서대로 적용한다.

    ⚠ 왜 "같은 길이"를 고집하는가: 이 본문 문단에는 메모(코멘트) 인라인
    필드 마커가 여러 개 남아있다(K 단독이면 그 마커 자체가 감싸는 내용을
    비우는 것이고, U 단독이면 마커는 그대로 둔 채 그 *밖의* 순수 텍스트만
    바꾼다). 문단의 글자 수를 조금이라도 늘리거나 줄이면, 뒤따라오는 다른
    메모 마커들의 절대 위치가 밀리는데, 이 문단의 LINE_SEG(줄 나눔 캐시)는
    일부러 손대지 않고 원본 그대로 두므로(1줄로 축소하면 겹쳐 그려지는
    문제가 있어 그렇게 못함), 마커 위치가 밀리면 캐시와 실제 위치가
    어긋난다. 실측 결과, 이 상태에서 hwp5 파서가 문단을 다시 줄 단위로
    나누다가 크래시했다(TypeError: unhashable type: 'slice' — 필드 마커
    구간이 통째로 잘려야 하는데 줄 경계가 그 중간을 가리키게 됨). 그래서
    지우는 자리는 완전히 없애지 않고 같은 길이의 공백으로 채워, 이 문단
    안의 모든 글자 위치(따라서 모든 마커의 절대 위치)를 원본과 정확히
    똑같이 유지한다 — 이미 검증된 "대학명 자리를 같은 길이 문자열로
    바꾸기"와 동일한, 안전이 확인된 패턴이다.
    """
    for old, new_visible, expect in edits:
        if len(new_visible) > len(old):
            raise ExclusiveSupplyError(f"{what}: 치환 결과가 원문보다 깁니다: {old!r}")
        padded = new_visible + " " * (len(old) - len(new_visible))
        n = _replace_all(records, old, padded)
        if n != expect:
            raise ExclusiveSupplyError(
                f"{what}: 치환 횟수가 예상과 다릅니다({n} != {expect}): {old!r}"
            )


def _edit_body_k_only(records: List[Record]) -> None:
    """K-NSSE 단독 본문 편집.

    UICA 메모 앵커(CTRL_HEADER)와 그 인라인 필드 마커는 절대 건드리지 않고,
    메모가 감싸는 "내용"만 같은 길이의 공백으로 비운다(완전히 비우면 손상됨
    — 실측 확인). 문단 전체 글자 수를 원본과 정확히 똑같이 유지해야 하므로
    (_apply_same_length_edits 참고), 배재대학교 참고본과 완전히 같은 문구를
    만들 수는 없고, 지워진 자리에 공백이 남는 것을 감수한다.
    """
    _apply_same_length_edits(records, [
        (_WRAP_UICA_SENTENCE, "", 1),
        (_WRAP_UICA_SHORT, "", 3),
        (_WRAP_INNOVATION, "", 1),
    ], "본문(K 단독) 메모 구간 비우기")


def _edit_body_u_only(records: List[Record]) -> None:
    """UICA 단독 본문 편집.

    UICA 관련 메모 구간(본문 안 5곳)은 원본 그대로 둔다 — 메모 앵커도
    내용도 전혀 건드리지 않으므로 가장 안전하다. K-NSSE 관련 순수 텍스트
    (메모로 감싸여 있지 않음)만 같은 길이의 공백으로 비운다(문단 전체
    글자 수를 그대로 유지해야 하므로, 완전히 지우지 않고 공백으로 채운다).
    """
    _apply_same_length_edits(records, [
        ("학부교육의 질과 성과 진단 및 분석’", "대학 혁신역량 진단 및 분석’", 1),
        ("학부교육 실태조사(K-NSSE)와 ", "", 3),
        (
            "학부교육 실태조사(K-NSSE)는 대학생의 학습과정 및 대학생활 경험을 "
            "진단하는 조사이며, 이를 통해 학부교육의 질과 성과를 진단할 수 있습니다. ",
            "",
            1,
        ),
        ("학부교육의 질과 성과, ", "", 1),
    ], "본문(UICA 단독) 순수 텍스트 비우기")


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

    # 0) 실무용 메모 6개(본문 UICA 문구 5개 + 계약명 자리 1개)는 대학에 보일
    #    필요가 없으므로 "내용"만 안 보이게 비운다. 앵커(CTRL_HEADER)는 절대
    #    건드리지 않는다 — 건드리면 문서 보안설정 [높음]에서 파일이 열리지
    #    않는다(실측 확인, 모듈 docstring 참고).
    strip_memo_controls_safe(records)

    # 1) 계약명(자문계약명)
    subject = subject_override(quote)
    if subject:
        if replace_literal_everywhere(records, _SUBJECT_DEFAULT, f"{subject} ") != 1:
            raise ExclusiveSupplyError("양식에서 자문계약명 자리를 찾지 못했습니다.")

    # 2) 본문 문단: 단일 도구면 관련 없는 문구만 안전하게 다듬는다(K+U 결합이면
    #    손대지 않고, 대학명은 3번 단계의 전역 치환으로 채워짐).
    if tools == {"K"}:
        _edit_body_k_only(records)
    elif tools == {"U"}:
        _edit_body_u_only(records)

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
