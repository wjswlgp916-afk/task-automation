"""PDF 렌더러 공용 유틸 (한글 폰트 등록 · 도장 이미지 경로).

견적서·거래명세서·대금청구서 등 여러 PDF 렌더러가 공유한다.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT = "NanumGothic"
FONT_BOLD = "NanumGothic-Bold"
_FONT_PATHS = [
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", FONT),
    ("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", FONT_BOLD),
]

STAMP_PATH = Path(__file__).parent / "templates" / "stamp.png"


def register_fonts() -> str:
    """나눔고딕 폰트를 등록하고, 실제 사용할 굵은 글꼴 이름을 반환한다.

    폰트 파일이 없는 환경이면 기본 폰트로 대체하고 일반 글꼴 이름을 반환한다.
    """
    for path, name in _FONT_PATHS:
        if name not in pdfmetrics.getRegisteredFontNames() and Path(path).exists():
            pdfmetrics.registerFont(TTFont(name, path))
    if FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFontFamily(FONT, normal="Helvetica")
    return FONT_BOLD if FONT_BOLD in pdfmetrics.getRegisteredFontNames() else FONT


def format_amount(v: int) -> str:
    return f"{v:,}" if v else "0"
