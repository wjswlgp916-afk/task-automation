"""자문 계약서 렌더러 (HWP 전용).

최대 조합(K-NSSE P+부가1·2, UICA P+부가1) 계약서 하나를 템플릿으로 삼아,
견적 등급코드에 맞게 자문범위·제공자료·특이사항을 자동으로 조정한다.
18개 법조항 본문은 손대지 않고, 대학명·금액·날짜·설문기준만 채운다.

등급별 규칙 (실제 양식 3종을 비교해 도출):
  * 프리미어(P): 자문범위 기본 3항목(대학간·대학내·성장분석) + 선택 부가서비스
  * 베이직(B)  : 자문범위 '대학 간 비교' 1항목만
  * 제공자료 보고서 문구: 프리미어=대시보드, 베이직="(excel 파일)"
  * 특이사항 설문기준: 도구가 있으면 표시(K→재학생, U→교수·직원), 등급 무관
  * 베이직은 단독 계약 대상이 아니며, 반대 도구가 프리미어일 때만 함께 실린다.

extra 딕셔너리:
  contract_date        계약체결일 (필수, 대학마다 다름)
  payment_due          납부기한   (기본 2027-02-13, 대학 회계마감 따라 변경 가능)
  period_start/period_end  자문기간 (기본 2026-09-01 ~ 2027-01-31)
  k_respondents        K-NSSE 재학생 최소 응답 (미지정 시 엑셀에서 조회)
  u_professors/u_staff UICA 교수·직원 최소 응답 (미지정 시 엑셀에서 조회)
"""

from __future__ import annotations

import re
import struct
import zlib
from datetime import date as date_cls
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import cfbf, survey_criteria
from .engine import Quote, parse_token, subject_override
from .hwp_writer import (
    PARA_HEADER,
    PARA_TEXT,
    PARA_CHAR_SHAPE,
    LIST_HEADER,
    CTRL_HEADER,
    MEMO_LIST,
    Record,
    parse_records,
    serialize_records,
    replace_literal_everywhere,
    set_plain_text,
    text_of,
)

_MEMO_CTRL_ID = b"knu%"
# 인라인 메모 마커: 8워드 컨트롤 블록. 시작=코드3, 끝=코드4, 두 번째 워드가
# 0x6d65('me'=MEMO) 이면 메모다. 다른 필드(구역/단 정의 등)와 구분된다.
_MEMO_MARK_WORD1 = 0x6D65
_EIGHT_WIDE_CTRL = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23}

# 기본값 (양식 그대로)
DEFAULT_PERIOD_START = date_cls(2026, 9, 1)
DEFAULT_PERIOD_END = date_cls(2027, 1, 31)
DEFAULT_PAYMENT_DUE = date_cls(2027, 2, 13)

# 양식의 정확한 placeholder 리터럴
_SUBJECT = "학부교육의 질과 성과 진단 및 분석"
_AMOUNT_NET = "7,000,000"       # 자문대가 VAT 별도
_AMOUNT_GROSS = "7,700,000"     # 자문료 VAT 포함
_PERIOD_SENTENCE = "2026년 9월 1일부터 2027년 1월 31일까지(만 5개월)"
_PERIOD_TABLE = "2026. 9. 1. ∼ 2027. 1. 31."
_PAYMENT_DUE = "2027년 2월 13일"
_CONTRACT_DATE_KOR = "2026년 0월 0일"
_CONTRACT_DATE_DOT = "2026. 0. 0."
_UNIV = "OO대학교"

# 제공자료 보고서 문구
_REPORT_PREMIUM = "대학별 보고서(보고서 열람이 가능한 대시보드 시스템은 자문 마감 연도 9월까지 열람 및 다운로드 가능함)"
_REPORT_BASIC = "대학별 보고서(excel 파일)"

# 자문범위 앵커(고유 문구)
_SCOPE_ANCHOR = "(대학 간 비교)"
_DELIV_ANCHOR = "대학별 보고서"
# 특이사항(설문기준) 문단 — 이 문단에는 인라인 메모 2개(재학생용·UICA용)가
# ***/** 자리에 걸려 있다. 도구 구성에 따라 이 문단을 순수 텍스트로 다시 쓰고
# 걸려 있던 그 2개 메모만 정확히 제거한다(다른 메모는 그대로 둔다).
_NOTES_ANCHOR = "분석 결과의 타당성"


class ContractError(RuntimeError):
    pass


def _default_template() -> Path:
    return Path(__file__).parent / "templates" / "contract_template.hwp"


# --------------------------------------------------------------------------- #
# 셀(누름틀 없는 표 칸) 안 문단 리스트 편집 도우미
# --------------------------------------------------------------------------- #
def _cell_region(records: List[Record], needle: str):
    """needle 을 포함하는 문단이 속한 셀(LIST_HEADER) 범위를 찾는다."""
    txt_idx = None
    for i, r in enumerate(records):
        if r.tag == PARA_TEXT and needle in text_of(r):
            txt_idx = i
            break
    if txt_idx is None:
        raise ContractError(f"양식에서 '{needle}' 위치를 찾지 못했습니다.")
    list_idx = None
    for j in range(txt_idx - 1, -1, -1):
        if records[j].tag == LIST_HEADER:
            list_idx = j
            break
    if list_idx is None:
        raise ContractError(f"'{needle}' 의 셀 헤더를 찾지 못했습니다.")
    lvl = records[list_idx].level
    end = len(records)
    for j in range(list_idx + 1, len(records)):
        if records[j].tag == LIST_HEADER and records[j].level <= lvl:
            end = j
            break
    return list_idx, end, lvl


def _cell_groups(records: List[Record], list_idx: int, end: int, lvl: int) -> List[List[int]]:
    """셀 안의 문단들을 (레코드 인덱스 묶음) 리스트로 나눈다."""
    groups: List[List[int]] = []
    cur: Optional[List[int]] = None
    for i in range(list_idx + 1, end):
        r = records[i]
        if r.tag == PARA_HEADER and r.level == lvl:
            if cur is not None:
                groups.append(cur)
            cur = [i]
        elif cur is not None:
            cur.append(i)
    if cur is not None:
        groups.append(cur)
    return groups


def _group_text_rec(records: List[Record], group: List[int]) -> Optional[Record]:
    for i in group:
        if records[i].tag == PARA_TEXT:
            return records[i]
    return None


def _group_hdr_rec(records: List[Record], group: List[int]) -> Optional[Record]:
    for i in group:
        if records[i].tag == PARA_HEADER:
            return records[i]
    return None


def _rewrite_cell(records: List[Record], needle: str, decide: Callable[[str, dict], bool]) -> None:
    """셀 안 문단들을 decide() 판정대로 남기고, LIST_HEADER 문단수를 갱신한다.

    decide(text, state) -> keep? state 는 문단 순서를 따라가며 섹션 문맥을
    유지하는 데 쓰는 가변 딕셔너리.
    """
    list_idx, end, lvl = _cell_region(records, needle)
    groups = _cell_groups(records, list_idx, end, lvl)
    state: dict = {}
    kept_groups: List[List[int]] = []
    for g in groups:
        tr = _group_text_rec(records, g)
        if decide(text_of(tr) if tr else "", state):
            kept_groups.append(g)

    new_slice: List[Record] = [records[list_idx]]
    for g in kept_groups:
        for i in g:
            new_slice.append(records[i])

    p = bytearray(records[list_idx].payload)
    struct.pack_into("<h", p, 0, len(kept_groups))
    records[list_idx].payload = bytes(p)
    records[list_idx:end] = new_slice


# --------------------------------------------------------------------------- #
# 자문범위 / 제공자료 / 특이사항 조정
# --------------------------------------------------------------------------- #
def _selections(quote: Quote):
    sels = {parse_token(c).tool: parse_token(c) for c in quote.source_codes}
    return sels.get("K"), sels.get("U")


def _scope_decide(k_sel, u_sel):
    def decide(txt: str, st: dict) -> bool:
        if "K-NSSE" in txt and "실태조사" in txt:
            st["sec"] = "K"; st["peer"] = False
            return k_sel is not None
        if "UICA" in txt and "진단조사" in txt:
            st["sec"] = "U"; st["peer"] = False
            return u_sel is not None
        sel = k_sel if st.get("sec") == "K" else u_sel
        if "(대학 간 비교)" in txt:
            st["peer"] = False
            return sel is not None
        if "(대학 내 비교)" in txt or "(성장 분석)" in txt:
            st["peer"] = False
            return sel is not None and sel.grade == "P"
        if "(단과대학별" in txt:
            st["peer"] = False
            return k_sel is not None and k_sel.grade == "P" and 1 in k_sel.addons
        if "Peer Benchmarking" in txt:
            if st.get("sec") == "K":
                keep = k_sel is not None and k_sel.grade == "P" and 2 in k_sel.addons
            else:
                keep = u_sel is not None and u_sel.grade == "P" and 1 in u_sel.addons
            st["peer"] = keep
            return keep
        if txt.strip().startswith("이 때 대학군"):
            return st.get("peer", False)
        return True
    return decide


def _deliv_decide(k_sel, u_sel):
    def decide(txt: str, st: dict) -> bool:
        if "K-NSSE" in txt and "실태조사" in txt:
            st["sec"] = "K"
            return k_sel is not None
        if "UICA" in txt and "진단조사" in txt:
            st["sec"] = "U"
            return u_sel is not None
        sel = k_sel if st.get("sec") == "K" else u_sel
        return sel is not None
    return decide


def _renumber_scope(records: List[Record], k_sel, u_sel) -> None:
    list_idx, end, lvl = _cell_region(records, _SCOPE_ANCHOR)
    groups = _cell_groups(records, list_idx, end, lvl)
    sec_no = 0
    item_no = 0
    for g in groups:
        hdr = _group_hdr_rec(records, g)
        tr = _group_text_rec(records, g)
        if not (hdr and tr):
            continue
        t = text_of(tr)
        if "K-NSSE" in t and "실태조사" in t:
            sec_no += 1; item_no = 0
            set_plain_text(hdr, tr, f"{sec_no}. 학부교육 실태조사(K-NSSE)")
        elif "UICA" in t and "진단조사" in t:
            sec_no += 1; item_no = 0
            set_plain_text(hdr, tr, f"{sec_no}. 대학 혁신역량 진단조사(UICA)")
        elif t.strip().startswith("이 때 대학군"):
            continue
        else:
            m = re.match(r"^\s*\d+\)\s*(.*)$", t, re.S)
            if m:
                item_no += 1
                set_plain_text(hdr, tr, f"  {item_no}) {m.group(1)}")


def _adjust_deliverables(records: List[Record], k_sel, u_sel) -> None:
    """제공자료: 섹션 번호 재부여 + 베이직 도구의 보고서 문구를 excel 로 교체."""
    list_idx, end, lvl = _cell_region(records, _DELIV_ANCHOR)
    groups = _cell_groups(records, list_idx, end, lvl)
    sec_no = 0
    cur_grade = None
    for g in groups:
        hdr = _group_hdr_rec(records, g)
        tr = _group_text_rec(records, g)
        if not (hdr and tr):
            continue
        t = text_of(tr)
        if "K-NSSE" in t and "실태조사" in t:
            sec_no += 1
            cur_grade = k_sel.grade if k_sel else None
            set_plain_text(hdr, tr, f"{sec_no}. 학부교육 실태조사(K-NSSE) ")
        elif "UICA" in t and "진단조사" in t:
            sec_no += 1
            cur_grade = u_sel.grade if u_sel else None
            set_plain_text(hdr, tr, f"{sec_no}. 대학 혁신역량 진단조사(UICA)")
        elif "대학별 보고서" in t:
            new = _REPORT_BASIC if cur_grade == "B" else _REPORT_PREMIUM
            m = re.match(r"^\s*(\d+\))\s*", t)
            prefix = m.group(1) + " " if m else "1) "
            set_plain_text(hdr, tr, f"{prefix}{new}")


def _strip_para_memo_markers(hdr: Record, txt: Record, cs: Optional[Record]) -> bool:
    """한 문단에서 인라인 메모 마커(코드3/4 + word1=0x6d65)를 제거하고 텍스트는
    남긴다. 마커(8워드)가 빠지면서 글자 수·글자모양(CHAR_SHAPE) 위치가 앞으로
    당겨지므로 이를 함께 보정한다(위치가 텍스트 길이를 넘어가면 '파일 손상').

    이 문단에 메모 마커가 있었으면 True.
    """
    units = list(struct.unpack(f"<{len(txt.payload) // 2}H", txt.payload))
    new: List[int] = []
    removed_at: List[int] = []          # 마커가 제거된 원본 위치들
    i = 0
    while i < len(units):
        c = units[i]
        if c in (3, 4) and i + 1 < len(units) and units[i + 1] == _MEMO_MARK_WORD1:
            removed_at.append(i)
            i += 8
        elif c in _EIGHT_WIDE_CTRL:
            new.extend(units[i:i + 8])   # 메모가 아닌 필드는 그대로 둔다
            i += 8
        else:
            new.append(c)
            i += 1
    if not removed_at:
        return False

    txt.payload = b"".join(struct.pack("<H", u) for u in new)
    (nchars,) = struct.unpack("<I", hdr.payload[0:4])
    nchars = (nchars & 0x80000000) | (len(new) & 0x7FFFFFFF)
    hdr.payload = struct.pack("<I", nchars) + hdr.payload[4:]

    if cs is not None:
        n = len(cs.payload) // 8
        entries = [struct.unpack_from("<II", cs.payload, k * 8) for k in range(n)]
        shifted: Dict[int, int] = {}
        for pos, cid in entries:
            removed_before = 8 * sum(1 for rp in removed_at if rp < pos)
            np = max(0, pos - removed_before)
            shifted[np] = cid            # 위치가 겹치면 뒤 항목이 이김
        new_entries = sorted(shifted.items())
        cs.payload = b"".join(struct.pack("<II", p, c) for p, c in new_entries)
        # PARA_HEADER 의 글자모양 개수([12:14]) 갱신
        hdr.payload = hdr.payload[:12] + struct.pack("<H", len(new_entries)) + hdr.payload[14:]
    return True


def _remove_all_memos(records: List[Record]) -> int:
    """문서의 모든 한글 메모(실무 안내용)를 안전하게 제거한다.

    1) 문단마다 인라인 메모 마커를 제거(+글자모양 보정). 여러 문단에 걸친
       메모(통지처 등)도 마커를 종류로 찾아 지우므로 짝 없는 마커가 남지 않는다.
    2) 메모 컨트롤(CTRL_HEADER knu%) 전부 제거.
    3) 메모 내용(MEMO_LIST 블록) 전부 제거.
    제거한 메모(컨트롤) 개수를 반환한다.
    """
    i = 0
    while i < len(records):
        r = records[i]
        if r.tag == PARA_TEXT:
            hdr = None
            for j in range(i - 1, -1, -1):
                if records[j].tag == PARA_HEADER:
                    hdr = records[j]
                    break
                if records[j].tag == PARA_TEXT:
                    break
            cs = None
            for j in range(i + 1, len(records)):
                if records[j].tag == PARA_CHAR_SHAPE:
                    cs = records[j]
                    break
                if records[j].tag in (PARA_HEADER, PARA_TEXT):
                    break
            if hdr is not None:
                _strip_para_memo_markers(hdr, txt=r, cs=cs)
        i += 1

    removed = sum(1 for r in records
                  if r.tag == CTRL_HEADER and r.payload[:4] == _MEMO_CTRL_ID)
    records[:] = [r for r in records
                  if not (r.tag == CTRL_HEADER and r.payload[:4] == _MEMO_CTRL_ID)]
    out: List[Record] = []
    i, n = 0, len(records)
    while i < n:
        r = records[i]
        if r.tag == MEMO_LIST:
            base = r.level
            j = i + 1
            while j < n and records[j].tag != MEMO_LIST and records[j].level >= base:
                j += 1
            i = j
            continue
        out.append(r)
        i += 1
    records[:] = out
    return removed


def _edit_special_notes(records: List[Record], k_sel, u_sel,
                        mins: survey_criteria.SurveyCriteria) -> None:
    """특이사항 문단을 도구 구성에 맞는 순수 텍스트로 다시 쓴다(메모는 이미 제거됨).

    K+U → 재학생·교수·직원, K 단독 → 재학생, U 단독 → 교수·직원.
    대학명(OO대학교)은 이후 전역 치환에서 함께 바뀐다.
    """
    parts: List[str] = []
    if k_sel is not None:
        parts.append(f"재학생 {mins.k_respondents}명 이상")
    if u_sel is not None:
        parts.append(f"교수 {mins.u_professors}명 이상, 직원 {mins.u_staff}명 이상")
    joined = " ".join(parts)
    text = (
        f"분석 결과의 타당성과 신뢰성을 위해 {_UNIV}는 {joined}의 응답 자료를 "
        f"확보해야 함. {_UNIV}와 자문책임자가 합의한 응답 자료를 확보하지 못할 시 "
        f"대학별 분석과 보고서가 제공되지 않을 수 있음"
    )
    for i, r in enumerate(records):
        if r.tag == PARA_TEXT and _NOTES_ANCHOR in text_of(r):
            hdr = None
            for j in range(i - 1, -1, -1):
                if records[j].tag == PARA_HEADER:
                    hdr = records[j]
                    break
                if records[j].tag == PARA_TEXT:
                    break
            if hdr is None:
                raise ContractError("특이사항 문단 헤더를 찾지 못했습니다.")
            set_plain_text(hdr, r, text)
            return
    raise ContractError("양식에서 특이사항(설문기준) 위치를 찾지 못했습니다.")


# --------------------------------------------------------------------------- #
# 입력값 정리
# --------------------------------------------------------------------------- #
def _dates(extra: Optional[dict]):
    extra = extra or {}
    contract_date = extra.get("contract_date")
    if not contract_date:
        raise ContractError("자문 계약서에는 계약체결일이 필요합니다.")
    period_start = extra.get("period_start") or DEFAULT_PERIOD_START
    period_end = extra.get("period_end") or DEFAULT_PERIOD_END
    payment_due = extra.get("payment_due") or DEFAULT_PAYMENT_DUE
    return contract_date, period_start, period_end, payment_due


def _mins(quote: Quote, k_sel, u_sel, extra: Optional[dict]) -> survey_criteria.SurveyCriteria:
    """설문 최소인원: extra 로 개별 지정 가능, 없으면 엑셀에서 조회."""
    extra = extra or {}
    base = None
    if k_sel is not None or u_sel is not None:
        base = survey_criteria.lookup(quote.university)
    k = extra.get("k_respondents")
    p = extra.get("u_professors")
    s = extra.get("u_staff")

    def pick(val, attr):
        if val is not None:
            return int(val)
        if base is not None:
            return getattr(base, attr)
        return None

    kk = pick(k, "k_respondents")
    pp = pick(p, "u_professors")
    ss = pick(s, "u_staff")

    if k_sel is not None and kk is None:
        raise survey_criteria.SurveyCriteriaError(
            f"'{quote.university}' 의 K-NSSE 재학생 설문기준을 찾지 못했습니다. "
            "설문기준 엑셀에 추가하거나 직접 입력해 주세요."
        )
    if u_sel is not None and (pp is None or ss is None):
        raise survey_criteria.SurveyCriteriaError(
            f"'{quote.university}' 의 UICA 교수·직원 설문기준을 찾지 못했습니다. "
            "설문기준 엑셀에 추가하거나 직접 입력해 주세요."
        )
    return survey_criteria.SurveyCriteria(kk or 0, pp or 0, ss or 0)


def _months(start: date_cls, end: date_cls) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


# --------------------------------------------------------------------------- #
# 메인
# --------------------------------------------------------------------------- #
def render_hwp(quote: Quote, out_path: str | Path, template: Optional[Path] = None,
               extra: Optional[dict] = None) -> Path:
    """자문 계약서를 HWP 파일로 저장하고 경로를 반환한다. (PDF 없음)"""
    k_sel, u_sel = _selections(quote)
    if k_sel is None and u_sel is None:
        raise ContractError("계약 대상 도구가 없습니다.")
    if (k_sel is None or k_sel.grade == "B") and (u_sel is None or u_sel.grade == "B"):
        raise ContractError(
            "베이직 등급만으로는 계약서를 발급하지 않습니다 "
            "(한쪽이 프리미어일 때만 반대쪽 베이직을 함께 실을 수 있습니다)."
        )

    contract_date, period_start, period_end, payment_due = _dates(extra)
    mins = _mins(quote, k_sel, u_sel, extra)

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

    # 0) 실무 안내 메모 12개 전부 제거(계약담당자용 프로세스·기입/확인·설문기준
    #    작성법 등). 여러 문단에 걸친 메모까지 마커를 종류로 찾아 완전히 지우고
    #    글자모양 위치를 보정하므로, 짝 없는 마커나 위치 초과 없이 안전하다.
    _remove_all_memos(records)

    # 1) 자문범위·제공자료·특이사항 조정 (대학명 치환 전에 수행)
    _rewrite_cell(records, _SCOPE_ANCHOR, _scope_decide(k_sel, u_sel))
    _renumber_scope(records, k_sel, u_sel)
    _rewrite_cell(records, _DELIV_ANCHOR, _deliv_decide(k_sel, u_sel))
    _adjust_deliverables(records, k_sel, u_sel)
    _edit_special_notes(records, k_sel, u_sel, mins)

    # 2) 금액
    net = round(quote.grand_total / 1.1)
    def rep(old, new, what):
        if replace_literal_everywhere(records, old, new) < 1:
            raise ContractError(f"양식에서 '{what}' 자리를 찾지 못했습니다.")
    rep(_AMOUNT_NET, f"{net:,}", "자문대가(VAT별도)")
    rep(_AMOUNT_GROSS, f"{quote.grand_total:,}", "자문료(VAT포함)")

    # 3) 자문기간 / 납부기한 / 계약체결일
    months = _months(period_start, period_end)
    rep(_PERIOD_SENTENCE,
        f"{period_start.year}년 {period_start.month}월 {period_start.day}일부터 "
        f"{period_end.year}년 {period_end.month}월 {period_end.day}일까지(만 {months}개월)",
        "자문기간(본문)")
    rep(_PERIOD_TABLE,
        f"{period_start.year}. {period_start.month}. {period_start.day}. ∼ "
        f"{period_end.year}. {period_end.month}. {period_end.day}.",
        "자문기간(표)")
    rep(_PAYMENT_DUE,
        f"{payment_due.year}년 {payment_due.month}월 {payment_due.day}일",
        "납부기한")
    rep(_CONTRACT_DATE_KOR,
        f"{contract_date.year}년 {contract_date.month}월 {contract_date.day}일",
        "계약체결일(표지)")
    rep(_CONTRACT_DATE_DOT,
        f"{contract_date.year}. {contract_date.month}. {contract_date.day}.",
        "계약체결일(계획서)")

    # 4) 계약명 (U 단독이면 UICA 명칭)
    subject = subject_override(quote)
    if subject:
        rep(_SUBJECT, subject, "계약명")

    # 5) 대학명 치환 (한 문단에 여러 번 나오는 경우까지 모두)
    while replace_literal_everywhere(records, _UNIV, quote.university) > 0:
        pass

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
