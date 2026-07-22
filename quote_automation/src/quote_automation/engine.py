"""견적 엔진 · 신청등급 코드 -> 견적 데이터.

등급 코드 규칙
--------------
토큰 하나  :  ``<도구><_><등급>[_<부가서비스숫자들>]``
    K_B        K-NSSE 베이직
    K_P        K-NSSE 프리미어 (부가서비스 없음)
    K_P_1      K-NSSE 프리미어 + 1번 부가서비스
    K_P_12     K-NSSE 프리미어 + 1번,2번 부가서비스
    U_P_1      UICA 프리미어 + 1번 부가서비스

한 견적서(한 장)에 두 도구를 함께 넣을 때
    ``+`` 로 결합 :  ``K_P_12+U_B``

두 도구를 이용하되 견적서를 따로(2장) 달라고 하면
    코드를 각각 따로 넘긴다 (CLI 에서는 인자를 여러 개, 함수는 여러 번 호출).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as date_cls
from typing import List

from .catalog import (
    CATALOG,
    GRADE_BASIC,
    GRADE_PREMIER,
    PREMIER_BASE_PRICE,
    ADDON_PRICE,
    BASIC_PRICE,
    split_vat,
)
from .korean_num import number_to_korean, won_amount

_TOKEN_RE = re.compile(r"^([A-Z])_([BP])(?:_(\d+))?$")


class QuoteError(ValueError):
    """등급 코드가 잘못됐을 때 발생하는 예외."""


@dataclass
class LineItem:
    """견적서 품목 한 줄."""

    name: str                 # 품명 (본품명 또는 부가서비스명)
    spec: str                 # 규격: 'PREMIER' / 'BASIC' / '' (부가서비스)
    qty: int                  # 수량
    supply: int               # 공급가액
    vat: int                  # 세액
    kind: str                 # 'main' (본품) 또는 'addon' (부가서비스)

    @property
    def unit_price(self) -> int:
        """단가 (수량 1 기준 공급가액과 동일)."""
        return self.supply // self.qty if self.qty else self.supply


@dataclass
class ToolSelection:
    """한 도구에 대한 선택 (도구 + 등급 + 부가서비스)."""

    tool: str                 # 'K' / 'U'
    grade: str                # 'B' / 'P'
    addons: List[int] = field(default_factory=list)


@dataclass
class Quote:
    """견적서 한 장에 담기는 모든 데이터."""

    university: str
    issue_date: date_cls
    items: List[LineItem]
    source_codes: List[str] = field(default_factory=list)

    @property
    def total_supply(self) -> int:
        return sum(i.supply for i in self.items)

    @property
    def total_vat(self) -> int:
        return sum(i.vat for i in self.items)

    @property
    def grand_total(self) -> int:
        return self.total_supply + self.total_vat

    @property
    def korean_amount(self) -> str:
        return number_to_korean(self.grand_total)

    @property
    def total_won(self) -> str:
        return won_amount(self.grand_total)

    @property
    def tools(self) -> set:
        """포함된 설문도구 코드 집합 (예: {'K'}, {'U'}, {'K','U'})."""
        return {code.split("_", 1)[0] for code in self.source_codes}


def short_product_name(tool_code: str) -> str:
    """정식 품명에서 '(K-NSSE)'/'(UICA)' 같은 끝 괄호 표기를 뗀 사업명."""
    full = CATALOG[tool_code].product_name
    return re.sub(r"\([^)]*\)\s*$", "", full).strip()


def subject_override(quote: "Quote"):
    """계약명/건명을 바꿔야 하면 새 값을, 기본값을 유지하면 None 을 반환한다.

    규칙(사용자 확정): K 단독 또는 K+U 결합이면 기본값(K-NSSE 사업명) 유지,
    U 단독이면 "대학 혁신역량 진단 및 분석"으로 교체.
    견적서·대금청구서·계약보증금 지급각서가 공유한다.
    """
    if quote.tools == {"U"}:
        return short_product_name("U")
    return None


def parse_token(token: str) -> ToolSelection:
    """등급 토큰 하나를 파싱·검증한다 (예: 'K_P_12')."""
    token = token.strip().upper()
    m = _TOKEN_RE.match(token)
    if not m:
        raise QuoteError(
            f"등급 코드 형식이 잘못되었습니다: {token!r} "
            f"(예: K_P_12, U_B, K_P_1)"
        )
    tool_code, grade, digits = m.group(1), m.group(2), m.group(3)

    if tool_code not in CATALOG:
        valid = ", ".join(CATALOG)
        raise QuoteError(f"알 수 없는 설문도구 '{tool_code}' (가능: {valid})")

    tool = CATALOG[tool_code]

    addons: List[int] = []
    if digits:
        if grade == GRADE_BASIC:
            raise QuoteError(
                f"{token}: 베이직(B)에는 부가서비스를 붙일 수 없습니다."
            )
        for ch in digits:
            num = int(ch)
            if num not in tool.addons:
                valid = ", ".join(str(k) for k in sorted(tool.addons))
                raise QuoteError(
                    f"{token}: '{tool_code}' 도구에 {num}번 부가서비스가 없습니다 "
                    f"(가능: {valid})"
                )
            if num in addons:
                raise QuoteError(f"{token}: 부가서비스 {num}번이 중복되었습니다.")
            addons.append(num)

    return ToolSelection(tool=tool_code, grade=grade, addons=addons)


def parse_codes(code: str) -> List[ToolSelection]:
    """한 견적서용 코드 문자열을 파싱한다 ('+' 로 도구 결합).

    예: ``"K_P_12+U_B"`` -> [K 선택, U 선택]
    """
    tokens = [t for t in re.split(r"[+,]", code) if t.strip()]
    if not tokens:
        raise QuoteError("빈 등급 코드입니다.")

    selections = [parse_token(t) for t in tokens]

    seen = set()
    for sel in selections:
        if sel.tool in seen:
            raise QuoteError(
                f"한 견적서에 '{sel.tool}' 도구가 두 번 들어갈 수 없습니다. "
                f"따로 견적서를 원하면 코드를 각각 넘기세요."
            )
        seen.add(sel.tool)

    return selections


def _selection_to_items(sel: ToolSelection) -> List[LineItem]:
    """도구 선택 하나를 품목 줄 목록으로 변환한다."""
    tool = CATALOG[sel.tool]
    items: List[LineItem] = []

    if sel.grade == GRADE_PREMIER:
        supply, vat = split_vat(PREMIER_BASE_PRICE)
        items.append(
            LineItem(tool.product_name, "PREMIER", 1, supply, vat, "main")
        )
        for num in sel.addons:
            a_supply, a_vat = split_vat(ADDON_PRICE)
            items.append(
                LineItem(tool.addons[num], "", 1, a_supply, a_vat, "addon")
            )
    else:  # BASIC
        supply, vat = split_vat(BASIC_PRICE)
        items.append(
            LineItem(tool.product_name, "BASIC", 1, supply, vat, "main")
        )

    return items


def build_quote(
    university: str,
    code: str,
    issue_date: date_cls | None = None,
) -> Quote:
    """대학명과 등급 코드로 견적서 데이터를 만든다.

    Parameters
    ----------
    university : 대학명 (예: '서원대학교')
    code       : 한 견적서용 등급 코드 (예: 'K_P_12+U_B')
    issue_date : 발급일자. 생략하면 오늘 날짜.
    """
    if not university or not university.strip():
        raise QuoteError("대학명이 비어 있습니다.")
    if issue_date is None:
        issue_date = date_cls.today()

    selections = parse_codes(code)
    items: List[LineItem] = []
    for sel in selections:
        items.extend(_selection_to_items(sel))

    return Quote(
        university=university.strip(),
        issue_date=issue_date,
        items=items,
        source_codes=[s.strip().upper() for s in re.split(r"[+,]", code) if s.strip()],
    )
