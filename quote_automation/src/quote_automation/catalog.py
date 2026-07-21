"""견적서 카탈로그 · 설정.

이 파일 하나만 고치면 회사 정보 / 설문도구 / 부가서비스 / 가격이 모두 바뀐다.
서비스가 늘거나 가격이 바뀌면 여기만 수정하면 되고, 나머지 코드는 건드릴 필요가 없다.

가격 규칙 (사용자 확인 완료)
---------------------------
* 모든 금액은 "부가세 포함가"로 표기하고, 내부에서 공급가액 / 세액(10%)으로 분리한다.
* 프리미어(PREMIER) 기본 : 2,200,000원 (공급가 2,000,000 + VAT 200,000)
* 부가서비스 각 1개당      : 1,100,000원 (공급가 1,000,000 + VAT 100,000)
* 베이직(BASIC)            : 0원
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


# --------------------------------------------------------------------------- #
# 회사(공급자) 정보 — 견적서 상단 고정 영역
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Company:
    registration_no: str = "101-82-12009"          # 등록번호
    name: str = "성균관대학교 산학협력단"            # 상호
    ceo: str = "구자춘 (인)"                         # 대표자
    address: str = "경기도 수원시 장안구 서부로 2066"  # 소재지
    business_type: str = "서비스"                    # 업태
    business_item: str = "산학협력업무"               # 종목
    tel: str = "031-290-5295, FAX 290-5089"          # TEL


COMPANY = Company()


# --------------------------------------------------------------------------- #
# 부가세 포함가 기준 단가
# --------------------------------------------------------------------------- #
VAT_RATE = 0.10                     # 부가가치세율 10%
PREMIER_BASE_PRICE = 2_200_000     # 프리미어 기본 (부가세 포함)
ADDON_PRICE = 1_100_000            # 부가서비스 1개 (부가세 포함)
BASIC_PRICE = 0                    # 베이직


@dataclass(frozen=True)
class Tool:
    """설문도구 정의."""

    code: str                       # 코드 첫 글자 (K / U)
    product_name: str               # 견적서 품명
    # 부가서비스: {번호: 서비스명}. 프리미어에서만 선택 가능하다.
    addons: Dict[int, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# 설문도구 카탈로그
#   * 코드 첫 글자 : K = K-NSSE, U = UICA
#   * 부가서비스 번호 : 도구별로 독립 (K의 1과 U의 1은 다른 서비스)
#   * 여기 dict 에 항목을 추가하면 새 부가서비스가 바로 지원된다.
# --------------------------------------------------------------------------- #
CATALOG: Dict[str, Tool] = {
    "K": Tool(
        code="K",
        product_name="학부교육의 질과 성과 진단 및 분석(K-NSSE)",
        addons={
            1: "단과대학별 분석 및 보고서 제공",
            2: "Peer Benchmarking 분석 및 보고서 제공",
        },
    ),
    "U": Tool(
        code="U",
        product_name="대학 혁신역량 진단 및 분석(UICA)",
        addons={
            1: "Peer Benchmarking 분석 및 보고서 제공",
        },
    ),
}


# 등급 두 번째 글자
GRADE_BASIC = "B"      # 베이직
GRADE_PREMIER = "P"    # 프리미어


def split_vat(total_incl_vat: int) -> tuple[int, int]:
    """부가세 포함가 -> (공급가액, 세액).

    공급가액 = 포함가 / 1.1 (반올림), 세액 = 포함가 - 공급가액.
    이렇게 하면 공급가액 + 세액 == 포함가 가 항상 성립한다.
    """
    supply = round(total_incl_vat / (1 + VAT_RATE))
    vat = total_incl_vat - supply
    return supply, vat
