"""숫자 -> 한글 금액 표기 변환.

견적서의 "합계금액" 칸에 들어가는 한글 금액을 만든다.
예) 3_300_000 -> "삼백삼십만 원정"
    1_650_000 -> "일백육십오만 원정"
    2_200_000 -> "이백이십만 원정"
"""

from __future__ import annotations

_DIGITS = "영일이삼사오육칠팔구"
_SMALL_UNITS = ["", "십", "백", "천"]          # 만 미만 자리
_BIG_UNITS = ["", "만", "억", "조", "경"]      # 4자리 묶음 단위


def _four_digit_to_hangul(n: int) -> str:
    """0~9999 를 한글로. 0 이면 빈 문자열."""
    if n == 0:
        return ""
    result = ""
    for pos in range(3, -1, -1):          # 천, 백, 십, 일
        digit = (n // (10 ** pos)) % 10
        if digit == 0:
            continue
        result += _DIGITS[digit] + _SMALL_UNITS[pos]
    return result


def _amount_to_hangul(amount: int) -> str:
    """정수 금액을 순수 한글 숫자로만 변환한다 (접미사 없음). 0 이면 '영'."""
    if amount < 0:
        raise ValueError("금액은 음수가 될 수 없습니다.")
    if amount == 0:
        return "영"

    groups = []                            # 4자리씩 묶음 (낮은 자리부터)
    n = amount
    while n > 0:
        groups.append(n % 10000)
        n //= 10000

    parts = []
    for idx in range(len(groups) - 1, -1, -1):  # 높은 자리부터
        chunk = groups[idx]
        if chunk == 0:
            continue
        parts.append(_four_digit_to_hangul(chunk) + _BIG_UNITS[idx])

    return "".join(parts)


def number_to_korean(amount: int) -> str:
    """정수 금액을 '..원정' 한글 표기로 변환한다 (견적서·거래명세서용)."""
    if amount == 0:
        return "영 원정"
    return _amount_to_hangul(amount) + " 원정"


def number_to_korean_plain(amount: int) -> str:
    """'원정' 없이 순수 한글 숫자만 반환한다 (예: 대금청구서의 '삼백삼십만원').

    호출부에서 원하는 접미사("원" 등)를 직접 붙여 쓴다.
    """
    return _amount_to_hangul(amount)


def won_amount(amount: int) -> str:
    """'\\  3,300,000' 형태의 아라비아 숫자 금액 문자열."""
    return f"\\  {amount:,}"
