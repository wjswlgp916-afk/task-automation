"""성균관대학교 산학협력단 견적서 자동화 패키지.

신청등급 코드(예: ``K_P_12``, ``U_B``)를 입력하면 견적서를 HWP/PDF로 생성한다.
"""

from .engine import Quote, LineItem, build_quote, parse_codes
from .catalog import CATALOG

__all__ = ["Quote", "LineItem", "build_quote", "parse_codes", "CATALOG"]

__version__ = "0.1.0"
