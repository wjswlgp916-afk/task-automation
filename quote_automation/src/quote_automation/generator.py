"""고수준 API — 대학명 + 등급코드로 HWP / PDF 견적서 파일을 만든다."""

from __future__ import annotations

import re
from datetime import date as date_cls
from pathlib import Path
from typing import Iterable, List

from .engine import build_quote, Quote
from .hwp_writer import render_hwp
from .pdf_writer import render_pdf


def _safe(name: str) -> str:
    """파일명에 안전한 문자열로 변환."""
    return re.sub(r"[^\w가-힣.-]+", "_", name).strip("_")


def generate(
    university: str,
    code: str,
    out_dir: str | Path = "output",
    issue_date: date_cls | None = None,
    formats: Iterable[str] = ("hwp", "pdf"),
) -> List[Path]:
    """견적서 한 장을 만들어 생성된 파일 경로 목록을 반환한다.

    Parameters
    ----------
    university : 대학명
    code       : 한 견적서용 등급코드 (예: 'K_P_12+U_B')
    out_dir    : 출력 폴더
    issue_date : 발급일자 (기본 오늘)
    formats    : 'hwp', 'pdf' 중 원하는 것
    """
    quote = build_quote(university, code, issue_date)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = f"견적서_{_safe(university)}_{_safe(code)}_{quote.issue_date:%Y%m%d}"
    created: List[Path] = []

    fmts = {f.lower() for f in formats}
    if "hwp" in fmts:
        created.append(render_hwp(quote, out_dir / f"{stem}.hwp"))
    if "pdf" in fmts:
        created.append(render_pdf(quote, out_dir / f"{stem}.pdf"))
    return created


def summarize(quote: Quote) -> str:
    """생성 결과 요약 문자열 (콘솔 출력용)."""
    lines = [f"  대학  : {quote.university}",
             f"  일자  : {quote.issue_date:%Y-%m-%d}",
             f"  코드  : {'+'.join(quote.source_codes)}",
             "  품목  :"]
    for it in quote.items:
        spec = f"[{it.spec}]" if it.spec else "        "
        lines.append(f"    - {it.name} {spec}  공급 {it.supply:,} / VAT {it.vat:,}")
    lines.append(f"  합계  : {quote.grand_total:,} 원  ({quote.korean_amount})")
    return "\n".join(lines)
