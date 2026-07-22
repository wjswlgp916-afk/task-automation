"""고수준 API — 대학명 + 등급코드로 HWP / PDF 서류 파일을 만든다.

견적서·거래명세서처럼 같은 견적 데이터(대학명·등급코드·금액)를 공유하는
서류들을 ``documents.DOCUMENT_TYPES`` 레지스트리 기준으로 한 번에 생성한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as date_cls
from pathlib import Path
from typing import Iterable, List

from .documents import DOCUMENT_TYPES, DocumentType, template_path
from .engine import build_quote, Quote


@dataclass(frozen=True)
class GeneratedFile:
    doc_label: str    # 서류 이름 (예: '견적서', '거래명세서')
    path: Path


def _safe(name: str) -> str:
    """파일명에 안전한 문자열로 변환."""
    return re.sub(r"[^\w가-힣.-]+", "_", name).strip("_")


def generate(
    university: str,
    code: str,
    out_dir: str | Path = "output",
    issue_date: date_cls | None = None,
    formats: Iterable[str] = ("hwp", "pdf"),
    doc_types: Iterable[str] = ("quote",),
    extra: dict | None = None,
) -> List[GeneratedFile]:
    """대학명+등급코드로 선택한 서류들을 한 번에 만들어 파일 경로 목록을 반환한다.

    Parameters
    ----------
    university : 대학명
    code       : 한 견적서용 등급코드 (예: 'K_P_12+U_B')
    out_dir    : 출력 폴더
    issue_date : 발급일자 (기본 오늘)
    formats    : 'hwp', 'pdf' 중 원하는 것 (서류가 지원하는 형식만 실제로 생성됨)
    doc_types  : documents.DOCUMENT_TYPES 의 키 목록 (예: ['quote', 'transaction_statement'])
    extra      : 서류별 추가 입력값 (예: 계약보증금 지급각서의 계약 날짜).
                 documents.coerce_extra() 로 만든 딕셔너리.
    """
    quote = build_quote(university, code, issue_date)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    fmts = {f.lower() for f in formats}
    created: List[GeneratedFile] = []

    for doc_key in doc_types:
        if doc_key not in DOCUMENT_TYPES:
            valid = ", ".join(DOCUMENT_TYPES)
            raise ValueError(f"알 수 없는 서류 종류 '{doc_key}' (가능: {valid})")
        doc_type = DOCUMENT_TYPES[doc_key]
        stem = f"{doc_type.label}_{_safe(university)}_{_safe(code)}_{quote.issue_date:%Y%m%d}"

        if "hwp" in fmts:
            tpl = template_path(doc_type)
            path = doc_type.render_hwp(quote, out_dir / f"{stem}.hwp", tpl, extra=extra)
            created.append(GeneratedFile(doc_type.label, path))
        if "pdf" in fmts and doc_type.supports_pdf:
            path = doc_type.render_pdf(quote, out_dir / f"{stem}.pdf", extra=extra)
            created.append(GeneratedFile(doc_type.label, path))

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
