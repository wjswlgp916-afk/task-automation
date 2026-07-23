"""설문 참여기준(최소 응답 인원) 조회.

계약서(자문 계약서)의 마지막 '특이사항'에 들어가는 대학별 최소 응답 인원을
엑셀(data/survey_criteria.xlsx)에서 읽어온다. 엑셀 컬럼:
    대학명 | K-NSSE 참여 기준 | UICA 교수 참여 기준 | UICA 직원 참여 기준

이 값들은 기본값이며, 대학이 수정을 요청하면 계약 생성 시 직접 덮어쓸 수
있다(extra 로 전달). 대학이 엑셀에 없으면 자동 조회가 불가하므로, 그때는
값을 직접 넣도록 안내하는 예외를 던진다.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

_XLSX = Path(__file__).parent / "data" / "survey_criteria.xlsx"


@dataclass(frozen=True)
class SurveyCriteria:
    k_respondents: int      # K-NSSE 재학생 최소 응답 인원
    u_professors: int       # UICA 교수 최소 응답 인원
    u_staff: int            # UICA 직원 최소 응답 인원


class SurveyCriteriaError(RuntimeError):
    pass


def _norm(name: str) -> str:
    """대학명 비교용 정규화: 공백 제거."""
    return "".join((name or "").split())


@lru_cache(maxsize=1)
def _load() -> Dict[str, SurveyCriteria]:
    try:
        import openpyxl
    except ImportError as e:      # pragma: no cover
        raise SurveyCriteriaError(
            "설문기준 엑셀을 읽으려면 openpyxl 이 필요합니다."
        ) from e
    if not _XLSX.exists():
        raise SurveyCriteriaError(f"설문기준 엑셀이 없습니다: {_XLSX}")
    wb = openpyxl.load_workbook(str(_XLSX), data_only=True, read_only=True)
    ws = wb.active
    table: Dict[str, SurveyCriteria] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        name = str(row[0]).strip()
        try:
            k = int(row[1]); p = int(row[2]); s = int(row[3])
        except (TypeError, ValueError):
            continue
        table[_norm(name)] = SurveyCriteria(k, p, s)
    wb.close()
    return table


def lookup(university: str) -> Optional[SurveyCriteria]:
    """대학명으로 설문기준을 찾는다. 없으면 None."""
    return _load().get(_norm(university))


def require(university: str) -> SurveyCriteria:
    """설문기준을 찾지 못하면 안내 예외를 던진다."""
    got = lookup(university)
    if got is None:
        raise SurveyCriteriaError(
            f"'{university}' 의 설문 참여기준이 엑셀에 없습니다. "
            "설문기준 엑셀에 추가하거나, 최소 응답 인원을 직접 입력해 주세요."
        )
    return got
