"""견적 엔진 단위 테스트."""

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quote_automation.engine import (  # noqa: E402
    build_quote,
    parse_codes,
    parse_token,
    QuoteError,
)
from quote_automation.korean_num import number_to_korean  # noqa: E402


# --------------------------------------------------------------------------- #
# 등급 코드 파싱
# --------------------------------------------------------------------------- #
def test_parse_token_premier_with_addons():
    sel = parse_token("K_P_12")
    assert sel.tool == "K"
    assert sel.grade == "P"
    assert sel.addons == [1, 2]


def test_parse_token_basic():
    sel = parse_token("U_B")
    assert sel.tool == "U"
    assert sel.grade == "B"
    assert sel.addons == []


def test_parse_token_lowercase_ok():
    assert parse_token("k_p_1").tool == "K"


def test_basic_with_addon_rejected():
    with pytest.raises(QuoteError):
        parse_token("K_B_1")


def test_unknown_tool_rejected():
    with pytest.raises(QuoteError):
        parse_token("X_P")


def test_unknown_addon_rejected():
    with pytest.raises(QuoteError):
        parse_token("U_P_2")   # UICA 에는 2번 부가서비스가 없다


def test_duplicate_addon_rejected():
    with pytest.raises(QuoteError):
        parse_token("K_P_11")


def test_combined_codes():
    sels = parse_codes("K_P_12+U_B")
    assert [s.tool for s in sels] == ["K", "U"]


def test_duplicate_tool_in_one_quote_rejected():
    with pytest.raises(QuoteError):
        parse_codes("K_P+K_B")


# --------------------------------------------------------------------------- #
# 금액 계산 (제공된 실제 견적서 샘플과 대조)
# --------------------------------------------------------------------------- #
def test_sample1_kangnam():
    # 강남대: K-NSSE 프리미어 + Peer(2번), UICA 베이직 -> 3,300,000
    q = build_quote("강남대학교", "K_P_2+U_B", date(2025, 8, 19))
    assert q.grand_total == 3_300_000
    assert q.total_supply == 3_000_000
    assert q.total_vat == 300_000
    assert q.korean_amount == "삼백삼십만 원정"
    # 품목: K본품, Peer부가, UICA베이직
    assert len(q.items) == 3
    assert q.items[0].spec == "PREMIER"
    assert q.items[1].kind == "addon"
    assert q.items[2].spec == "BASIC"
    assert q.items[2].supply == 0


def test_sample2_gwangju():
    # 광주여대: UICA 프리미어 단독 -> 2,200,000
    q = build_quote("광주여자대학교", "U_P", date(2025, 8, 20))
    assert q.grand_total == 2_200_000
    assert q.korean_amount == "이백이십만 원정"
    assert len(q.items) == 1


def test_full_combo():
    # K 프리미어 + 부가 2개, U 프리미어 + 부가 1개
    q = build_quote("테스트대학교", "K_P_12+U_P_1")
    # (2.2M) + 2*(1.1M) + (2.2M) + (1.1M) = 7.7M
    assert q.grand_total == 7_700_000
    assert len(q.items) == 5


def test_basic_only_is_zero():
    q = build_quote("무료대학교", "U_B")
    assert q.grand_total == 0
    assert q.korean_amount == "영 원정"


def test_default_date_is_today():
    q = build_quote("오늘대학교", "U_P")
    assert q.issue_date == date.today()


# --------------------------------------------------------------------------- #
# 한글 금액 변환
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "amount,expected",
    [
        (3_300_000, "삼백삼십만 원정"),
        (2_200_000, "이백이십만 원정"),
        (1_650_000, "일백육십오만 원정"),
        (9_900_000, "구백구십만 원정"),
        (11_000_000, "일천일백만 원정"),
        (100_000_000, "일억 원정"),
        (0, "영 원정"),
    ],
)
def test_korean_number(amount, expected):
    assert number_to_korean(amount) == expected
