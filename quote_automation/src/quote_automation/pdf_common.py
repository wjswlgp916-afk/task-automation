"""PDF 렌더러 공용 유틸 (한글 폰트 등록 · 도장 이미지 경로).

견적서·거래명세서·대금청구서 등 여러 PDF 렌더러가 공유한다.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable

# 흰 배경을 투명 처리해 붉은 인영만 남기는 색상 키 마스크 (글자 위에 겹칠 때)
STAMP_WHITE_MASK = [230, 255, 230, 255, 230, 255]

# 원본 HWP 서류들은 본문에 '바탕'/'HY신명조'(명조·serif 계열)를 쓴다.
# PDF 도 같은 느낌을 내도록 나눔명조(serif)를 기본 글꼴로 쓴다.
FONT = "NanumMyeongjo"
FONT_BOLD = "NanumMyeongjo-Bold"
_FONT_PATHS = [
    ("/usr/share/fonts/truetype/nanum/NanumMyeongjo.ttf", FONT),
    ("/usr/share/fonts/truetype/nanum/NanumMyeongjoBold.ttf", FONT_BOLD),
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


class StampOverlay(Flowable):
    """도장 이미지를 바로 위 줄(대표자 '(인)')에 겹쳐 찍는다.

    reportlab flowable 은 겹침을 직접 지원하지 않으므로, 실제 도장보다 낮은
    높이만 차지하고 이미지를 위쪽(이전 줄)까지 올려 그린다. 흰 배경은
    색상 키 마스크로 투명 처리해 글자가 비쳐 보이도록 한다.
    """

    def __init__(self, path: str, size: float, x: float, overlap: float,
                mask=STAMP_WHITE_MASK):
        super().__init__()
        self.path = path
        self.size = size          # 도장 한 변 길이
        self.x = x                # 프레임 왼쪽 기준 x
        self.overlap = overlap    # 위 줄로 겹쳐 올라갈 높이
        # 흰 배경 RGB 이미지는 색상 키(STAMP_WHITE_MASK)로 흰 배경을
        # 투명 처리해야 하지만, 이미 알파 채널로 투명 배경을 가진 PNG는
        # 색상 키를 주면 오히려 검게 칠해진다 — 그런 이미지는 'auto' 를
        # 넘겨 PIL 이 알파 채널을 그대로 쓰게 한다.
        self.mask = mask

    def wrap(self, availWidth, availHeight):
        return (availWidth, max(0.0, self.size - self.overlap))

    def draw(self):
        # 로컬 원점(0,0)=박스 좌하단. y=0 에서 size 높이로 그리면
        # 위쪽 overlap 만큼 이전 줄에 겹쳐진다.
        self.canv.drawImage(
            self.path, self.x, 0, width=self.size, height=self.size,
            mask=self.mask, preserveAspectRatio=True,
        )


def stamp_x_over(text_before: str, target: str, value_start_x: float,
                 font: str, font_size: float, stamp_size: float) -> float:
    """값 문자열에서 ``target`` (예: '(인)') 중앙 위에 도장을 놓을 x 를 계산한다.

    text_before : target 앞의 문자열(예: '구자춘 ')
    value_start_x : 값 텍스트가 시작하는 x (프레임 왼쪽 기준)
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth
    center = (value_start_x
              + stringWidth(text_before, font, font_size)
              + stringWidth(target, font, font_size) / 2)
    return center - stamp_size / 2
