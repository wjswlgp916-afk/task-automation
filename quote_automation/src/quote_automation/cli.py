"""견적서 자동화 CLI.

사용 예
-------
# 한 견적서 (K-NSSE 프리미어 + 부가 1,2  와  UICA 베이직 을 한 장에)
python -m quote_automation --univ 서원대학교 --code K_P_12+U_B

# 두 도구를 이용하되 견적서를 따로 2장
python -m quote_automation --univ OO대학교 --code K_P_2 --code U_B

# 발급일자 지정, PDF 만
python -m quote_automation --univ OO대학교 --code U_P --date 2026-07-21 --format pdf
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from .catalog import CATALOG, PREMIER_BASE_PRICE, ADDON_PRICE
from .engine import build_quote, QuoteError
from .generator import generate, summarize


def _parse_date(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError("날짜 형식은 YYYY-MM-DD 입니다 (예: 2026-07-21)")


def _print_catalog() -> None:
    print("사용 가능한 등급 코드")
    print("=" * 50)
    for tcode, tool in CATALOG.items():
        name = {"K": "K-NSSE", "U": "UICA"}.get(tcode, tcode)
        print(f"\n[{tcode}] {name} — {tool.product_name}")
        print(f"    {tcode}_B         베이직 (0원)")
        print(f"    {tcode}_P         프리미어 ({PREMIER_BASE_PRICE:,}원, VAT포함)")
        for num, svc in sorted(tool.addons.items()):
            print(f"    {tcode}_P_{num}       + {svc} (+{ADDON_PRICE:,}원)")
    print("\n조합 예:  K_P_12+U_B  (한 장에 두 도구)")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="quote_automation",
        description="성균관대학교 산학협력단 견적서 자동 생성기",
    )
    p.add_argument("--univ", "--university", dest="university",
                   help="대학명 (예: 서원대학교)")
    p.add_argument("--code", dest="codes", action="append", default=[],
                   help="등급코드. 여러 번 쓰면 견적서가 따로 생성됩니다. "
                        "한 장에 두 도구는 '+' 로 결합 (예: K_P_12+U_B)")
    p.add_argument("--date", type=_parse_date, default=None,
                   help="발급일자 YYYY-MM-DD (기본: 오늘)")
    p.add_argument("--out", dest="out_dir", default="output", help="출력 폴더")
    p.add_argument("--format", dest="formats", action="append",
                   choices=["hwp", "pdf"], default=None,
                   help="출력 형식 (기본: hwp,pdf 둘 다)")
    p.add_argument("--list", action="store_true", help="등급 코드 목록 출력 후 종료")

    args = p.parse_args(argv)

    if args.list:
        _print_catalog()
        return 0

    if not args.university or not args.codes:
        p.error("--univ 와 --code 는 필수입니다. (등급 코드 목록: --list)")

    formats = args.formats or ["hwp", "pdf"]

    exit_code = 0
    for code in args.codes:
        try:
            quote = build_quote(args.university, code, args.date)
            paths = generate(args.university, code, args.out_dir, args.date, formats)
            print(f"\n✅ 견적서 생성 [{code}]")
            print(summarize(quote))
            for path in paths:
                print(f"  파일  : {path}")
        except QuoteError as e:
            print(f"\n❌ [{code}] 오류: {e}", file=sys.stderr)
            exit_code = 1
        except Exception as e:  # noqa: BLE001
            print(f"\n❌ [{code}] 생성 실패: {type(e).__name__}: {e}", file=sys.stderr)
            exit_code = 2
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
