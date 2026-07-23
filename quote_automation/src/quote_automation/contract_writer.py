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


_LAST_PARA_BIT = 0x80000000  # PARA_HEADER 첫 DWORD의 최상위 비트: '이 목록의 마지막 문단' 표시


def _fix_last_paragraph_flag(records: List[Record], kept_groups: List[List[int]]) -> None:
    """셀 안 마지막으로 남은 문단에만 '마지막 문단' 비트를 세운다.

    한글은 각 셀(문단 목록)의 마지막 문단 PARA_HEADER 에 이 비트를 표시해
    둔다. 뒤쪽 문단을 통째로 지워 새로운 문단이 목록의 끝이 되면 이 비트를
    옮겨주지 않는 한 한글이 "파일 손상"으로 판정한다(실측으로 확인한 문제).
    """
    for gi, g in enumerate(kept_groups):
        hdr = _group_hdr_rec(records, g)
        if hdr is None:
            continue
        (val,) = struct.unpack("<I", hdr.payload[0:4])
        is_last = gi == len(kept_groups) - 1
        new_val = (val | _LAST_PARA_BIT) if is_last else (val & ~_LAST_PARA_BIT)
        hdr.payload = struct.pack("<I", new_val) + hdr.payload[4:]


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

    _fix_last_paragraph_flag(records, kept_groups)

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


def _mark_row_recompute(records: List[Record], anchor: str) -> None:
    """anchor 셀이 속한 표 '행'의 모든 칸에 '높이 재계산' 속성 비트를 켠다.

    한글은 표에서 문단을 지우면 그 행의 높이를 다시 계산해 행의 모든 칸에
    같은 값을 적용한다. 우리는 정확한 높이를 계산하지 못하지만, 이 속성 비트
    (0x05000000)를 켜두면 한글이 파일을 열 때 스스로 높이를 재계산하므로
    저장된 높이 값이 옛것이어도 손상 없이 열린다(실측 확인).
    행 번호와 같은 표 레벨로 범위를 한정해 다른 표를 건드리지 않는다.
    """
    li, _, lvl = _cell_region(records, anchor)
    row = struct.unpack("<H", records[li].payload[10:12])[0]
    for r in records:
        if r.tag == LIST_HEADER and r.level == lvl and len(r.payload) >= 24:
            if struct.unpack("<H", r.payload[10:12])[0] == row:
                p = bytearray(r.payload)
                fl = struct.unpack("<I", p[4:8])[0]
                struct.pack_into("<I", p, 4, fl | 0x05000000)
                r.payload = bytes(p)


def _inline_memo_ids(rec: Record) -> List[int]:
    """PARA_TEXT 안 인라인 메모의 ID 목록. 메모 끝 마커(0x04 로 시작하는 8워드
    블록)의 6번째 워드에 메모 인스턴스 ID 가 들어있다."""
    u = struct.unpack(f"<{len(rec.payload) // 2}H", rec.payload)
    ids: List[int] = []
    i = 0
    while i < len(u):
        if u[i] == 4 and i + 5 < len(u) and u[i + 1] == _MEMO_MARK_WORD1:
            ids.append(u[i + 5])
            i += 8
        elif u[i] in _EIGHT_WIDE_CTRL:
            i += 8
        else:
            i += 1
    return ids


def _drop_memos(records: List[Record], ids: set) -> None:
    """지정한 ID 의 메모만 정확히 제거한다(인라인 마커는 문단을 순수 텍스트로
    다시 쓰면서 이미 사라졌다는 전제). 해당 메모의 CTRL_HEADER 와 MEMO_LIST
    블록을 지운다. 다른 메모는 그대로 둔다."""
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


def _edit_special_notes(records: List[Record], k_sel, u_sel,
                        mins: survey_criteria.SurveyCriteria) -> None:
    """특이사항 문단을 도구 구성에 맞는 순수 텍스트로 다시 쓰고, 그 문단에 걸려
    있던 인라인 메모 2개(재학생용·UICA용)만 정확히 제거한다.

    이 문단은 글자모양이 하나뿐이라 순수 텍스트로 다시 써도 안전하다.
    (나머지 실무 안내 메모는 인쇄되지 않는 코멘트라 그대로 둔다.)
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
            memo_ids = set(_inline_memo_ids(r))
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
            if memo_ids:
                _drop_memos(records, memo_ids)
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

    # 실무 안내 메모(프로세스·기입/확인 등)는 인쇄되지 않는 한글 코멘트라 그대로
    # 둔다(실제 배포되는 정상 계약서도 메모를 유지함). 특이사항에 걸린 메모 2개만
    # 내용을 바꿔야 해서 그 문단 안에서 정확히 제거한다.

    # 1) 자문범위·제공자료·특이사항 조정 (대학명 치환 전에 수행)
    _rewrite_cell(records, _SCOPE_ANCHOR, _scope_decide(k_sel, u_sel))
    _renumber_scope(records, k_sel, u_sel)
    _mark_row_recompute(records, _SCOPE_ANCHOR)
    _rewrite_cell(records, _DELIV_ANCHOR, _deliv_decide(k_sel, u_sel))
    _adjust_deliverables(records, k_sel, u_sel)
    _mark_row_recompute(records, _DELIV_ANCHOR)
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
