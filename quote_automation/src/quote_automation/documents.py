"""문서 종류 레지스트리.

견적서·거래명세서처럼 **같은 견적 데이터(대학명·등급코드·금액)를 공유**하는
서류들을 정의한다. 새 서류를 추가할 때는 이 파일에 항목 하나만 등록하면
CLI·웹 대시보드에 자동으로 나타난다.

거래명세서는 견적서와 표 구조(공급자 정보·품목 테이블·합계·도장)가 완전히
동일한 원본 hwp 양식이라, hwp_writer.render_hwp() 를 템플릿만 바꿔 그대로
재사용한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass(frozen=True)
class DocumentType:
    key: str                          # 내부 식별자 (URL/CLI 인자 등에 사용)
    label: str                        # 화면에 보여줄 이름
    template_filename: Optional[str]  # templates/ 안의 hwp 파일명 (None = 기본 견적서 양식)
    supports_pdf: bool                # PDF 출력을 지원하는지
    pdf_title: str = "견 적 서"        # PDF 상단 제목
    pdf_greeting: Optional[str] = "아래와 같이 견적합니다."  # None 이면 인사말 생략


_TEMPLATES_DIR = Path(__file__).parent / "templates"

DOCUMENT_TYPES: Dict[str, DocumentType] = {
    "quote": DocumentType(
        key="quote",
        label="견적서",
        template_filename=None,        # hwp_writer 의 기본 quote_template.hwp 사용
        supports_pdf=True,
        pdf_title="견 적 서",
        pdf_greeting="아래와 같이 견적합니다.",
    ),
    "transaction_statement": DocumentType(
        key="transaction_statement",
        label="거래명세서",
        template_filename="transaction_statement_template.hwp",
        supports_pdf=True,
        pdf_title="거 래 명 세 서",
        pdf_greeting=None,             # 원본 양식에 인사말 문구가 없음
    ),
}


def template_path(doc_type: DocumentType) -> Optional[Path]:
    """문서 종류의 hwp 템플릿 경로 (None 이면 hwp_writer 기본값 사용)."""
    if doc_type.template_filename is None:
        return None
    return _TEMPLATES_DIR / doc_type.template_filename
