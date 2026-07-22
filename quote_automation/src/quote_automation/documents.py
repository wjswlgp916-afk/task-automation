"""문서 종류 레지스트리.

견적서·거래명세서·대금청구서·계약보증금 지급각서처럼 **같은 견적 데이터
(대학명·등급코드·금액)를 공유**하는 서류들을 정의한다. 새 서류를 추가할 때는
이 파일에 항목 하나만 등록하면 CLI·웹 대시보드에 자동으로 나타난다.

일부 서류는 대학명·등급·발급일자만으로는 알 수 없는 **추가 입력**이 필요하다
(예: 계약보증금 지급각서의 계약 기간). 그런 값은 ``extra_fields`` 로 선언하면
대시보드/CLI 가 입력칸을 만들어 받고, 렌더 함수에 ``extra`` 딕셔너리로 전달한다.

모든 렌더 함수는 다음 형태를 따른다:
    render_hwp(quote, out_path, template=None, extra=None) -> Path
    render_pdf(quote, out_path, extra=None) -> Path
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import guaranty_writer, hwp_writer, invoice_writer, pdf_writer
from .engine import Quote


@dataclass(frozen=True)
class ExtraField:
    """서류별 추가 입력 항목 (대학명·등급·발급일자 외)."""

    key: str
    label: str
    kind: str = "date"          # 'date' | 'text'
    required: bool = False
    default: str = ""           # date 는 'YYYY-MM-DD' 문자열, '' = 없음
    help: str = ""


@dataclass(frozen=True)
class DocumentType:
    key: str
    label: str
    render_hwp: Callable
    render_pdf: Optional[Callable]
    template_filename: Optional[str] = None
    extra_fields: Tuple[ExtraField, ...] = ()

    @property
    def supports_pdf(self) -> bool:
        return self.render_pdf is not None


_TEMPLATES_DIR = Path(__file__).parent / "templates"

DOCUMENT_TYPES: Dict[str, DocumentType] = {
    "quote": DocumentType(
        key="quote",
        label="견적서",
        render_hwp=hwp_writer.render_hwp,
        render_pdf=partial(pdf_writer.render_pdf, title="견 적 서", greeting="아래와 같이 견적합니다."),
        template_filename=None,
    ),
    "transaction_statement": DocumentType(
        key="transaction_statement",
        label="거래명세서",
        render_hwp=hwp_writer.render_hwp,
        render_pdf=partial(pdf_writer.render_pdf, title="거 래 명 세 서", greeting=None),
        template_filename="transaction_statement_template.hwp",
    ),
    "invoice": DocumentType(
        key="invoice",
        label="대금청구서",
        render_hwp=invoice_writer.render_hwp,
        render_pdf=invoice_writer.render_pdf,
        template_filename="invoice_template.hwp",
    ),
    "guaranty": DocumentType(
        key="guaranty",
        label="계약보증금 지급각서",
        render_hwp=guaranty_writer.render_hwp,
        render_pdf=guaranty_writer.render_pdf,
        template_filename="guaranty_template.hwp",
        extra_fields=(
            ExtraField("contract_start", "계약 시작일", "date", required=True,
                       help="대학마다 바뀌는 계약 시작일"),
            ExtraField("contract_end", "계약 종료일", "date", default="2027-01-31",
                       help="보통 고정 (필요시 수정)"),
            ExtraField("commencement", "착수일", "date", default="2026-09-01",
                       help="보통 고정 (필요시 수정)"),
        ),
    ),
}


def template_path(doc_type: DocumentType) -> Optional[Path]:
    """문서 종류의 hwp 템플릿 경로 (None 이면 렌더 함수 기본값 사용)."""
    if doc_type.template_filename is None:
        return None
    return _TEMPLATES_DIR / doc_type.template_filename


def extra_fields_for(doc_keys) -> List[ExtraField]:
    """선택된 서류들에 필요한 추가 입력 항목 목록 (중복 제거, 순서 유지)."""
    seen: Dict[str, ExtraField] = {}
    for k in doc_keys:
        for f in DOCUMENT_TYPES[k].extra_fields:
            seen.setdefault(f.key, f)
    return list(seen.values())


def coerce_extra(doc_keys, raw: Dict[str, str]) -> dict:
    """폼/CLI 로 받은 문자열 값을 검증·변환한다 (날짜는 date 로).

    필수 항목이 비었거나 형식이 틀리면 ValueError 를 던진다.
    """
    out: dict = {}
    for f in extra_fields_for(doc_keys):
        val = (raw.get(f.key) or "").strip() or f.default
        if not val:
            if f.required:
                raise ValueError(f"'{f.label}' 값을 입력하세요.")
            continue
        if f.kind == "date":
            try:
                out[f.key] = datetime.strptime(val, "%Y-%m-%d").date()
            except ValueError:
                raise ValueError(f"'{f.label}' 날짜 형식이 잘못되었습니다 (YYYY-MM-DD).")
        else:
            out[f.key] = val
    return out
