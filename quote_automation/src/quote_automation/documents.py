"""문서 종류 레지스트리.

견적서·거래명세서·대금청구서처럼 **같은 견적 데이터(대학명·등급코드·금액)를
공유**하는 서류들을 정의한다. 새 서류를 추가할 때는 이 파일에 항목 하나만
등록하면 CLI·웹 대시보드에 자동으로 나타난다.

서류마다 원본 양식의 구조가 다를 수 있어(표 기반 vs 문장형 공문서),
HWP/PDF 렌더 함수를 문서 종류별로 직접 지정한다. 모든 렌더 함수는
아래 형태를 따른다:
    render_hwp(quote, out_path, template: Path | None) -> Path
    render_pdf(quote, out_path) -> Path
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Callable, Dict, Optional

from . import hwp_writer, invoice_writer, pdf_writer
from .engine import Quote

RenderHwpFn = Callable[[Quote, "Path | str", Optional[Path]], Path]
RenderPdfFn = Callable[[Quote, "Path | str"], Path]


@dataclass(frozen=True)
class DocumentType:
    key: str                          # 내부 식별자 (URL/CLI 인자 등에 사용)
    label: str                        # 화면에 보여줄 이름
    render_hwp: RenderHwpFn           # (quote, out_path, template) -> Path
    render_pdf: Optional[RenderPdfFn]  # None 이면 PDF 미지원
    template_filename: Optional[str] = None  # templates/ 안의 hwp 파일명 (None = 렌더 함수 기본값)

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
        template_filename=None,        # hwp_writer 의 기본 quote_template.hwp 사용
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
}


def template_path(doc_type: DocumentType) -> Optional[Path]:
    """문서 종류의 hwp 템플릿 경로 (None 이면 렌더 함수 기본값 사용)."""
    if doc_type.template_filename is None:
        return None
    return _TEMPLATES_DIR / doc_type.template_filename
