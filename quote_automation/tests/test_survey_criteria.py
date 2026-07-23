"""설문 참여기준 엑셀 조회 테스트."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quote_automation import survey_criteria  # noqa: E402


def test_lookup_known_university():
    c = survey_criteria.lookup("한성대학교")
    assert c is not None
    assert c.k_respondents == 200
    assert c.u_professors == 50
    assert c.u_staff == 50


def test_lookup_normalizes_whitespace():
    assert survey_criteria.lookup(" 한성대학교 ") == survey_criteria.lookup("한성대학교")


def test_lookup_unknown_returns_none():
    assert survey_criteria.lookup("존재하지않는대학교") is None


def test_require_raises_for_unknown():
    with pytest.raises(survey_criteria.SurveyCriteriaError):
        survey_criteria.require("존재하지않는대학교")


def test_all_rows_have_positive_numbers():
    table = survey_criteria._load()
    assert len(table) > 100          # 200여 개 대학
    for name, c in table.items():
        assert c.k_respondents > 0 and c.u_professors > 0 and c.u_staff > 0
