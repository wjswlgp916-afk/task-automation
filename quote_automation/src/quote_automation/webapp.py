"""견적서 웹 대시보드 (Flask).

설치 없이 주소로 접속해 클릭으로 견적서를 만드는 화면.
- 공용 비밀번호(환경변수 APP_PASSWORD)로 접근 제한
- 대학명·발급일자 입력 → K-NSSE / UICA 등급·부가서비스 선택 → HWP·PDF 생성·다운로드

로컬 실행:
    APP_PASSWORD=원하는비번  python -m quote_automation.webapp
    # 또는  flask --app quote_automation.webapp run
"""

from __future__ import annotations

import hmac
import os
import uuid
import tempfile
from datetime import date, datetime
from functools import wraps
from pathlib import Path
from typing import List, Tuple

from flask import (
    Flask, request, render_template, send_from_directory,
    Response, redirect, url_for, abort,
)

from .catalog import CATALOG, PREMIER_BASE_PRICE, ADDON_PRICE
from .engine import build_quote, Quote, QuoteError
from .generator import generate

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "webapp_templates"),
)

# 생성된 파일 임시 보관 (토큰 -> 폴더)
_DL_ROOT = Path(tempfile.gettempdir()) / "quote_automation_downloads"
_DL_ROOT.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# 공용 비밀번호 (HTTP Basic Auth)
# --------------------------------------------------------------------------- #
def _expected_password() -> str | None:
    """설정된 공용 비밀번호.

    Render 등에서 환경변수를 붙여넣을 때 끝에 공백/줄바꿈이 섞여 들어오는
    경우가 흔해서, 앞뒤 공백은 제거하고 비교한다.
    """
    pw = os.environ.get("APP_PASSWORD")
    return pw.strip() if pw else pw


def _check_auth(pw: str) -> bool:
    expected = _expected_password()
    if not expected:            # 비번 미설정 시 접근 허용(로컬 개발용)
        return True
    # hmac.compare_digest 는 비ASCII 문자가 섞인 str 비교를 지원하지 않으므로
    # (한글 비밀번호 등) UTF-8 바이트로 인코딩해 비교한다.
    return hmac.compare_digest((pw or "").encode("utf-8"), expected.encode("utf-8"))


def require_password(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        expected = _expected_password()
        if expected:
            auth = request.authorization
            if not auth or not _check_auth(auth.password):
                return Response(
                    "비밀번호가 필요합니다.", 401,
                    {"WWW-Authenticate": 'Basic realm="Quote Automation"'},
                )
        return f(*args, **kwargs)
    return wrapper


# --------------------------------------------------------------------------- #
# 화면
# --------------------------------------------------------------------------- #
def _tool_view():
    """템플릿에 넘길 도구/부가서비스 정보."""
    out = []
    labels = {"K": "K-NSSE", "U": "UICA"}
    for tc, tool in CATALOG.items():
        out.append({
            "code": tc,
            "label": labels.get(tc, tc),
            "product": tool.product_name,
            "addons": [{"num": n, "name": s} for n, s in sorted(tool.addons.items())],
        })
    return out


@app.route("/")
@require_password
def index():
    return render_template(
        "index.html",
        tools=_tool_view(),
        today=date.today().isoformat(),
        premier_price=f"{PREMIER_BASE_PRICE:,}",
        addon_price=f"{ADDON_PRICE:,}",
    )


def _code_for_tool(tool_code: str, form) -> str | None:
    """폼 입력에서 한 도구의 등급코드 문자열을 만든다. 미선택이면 None."""
    if not form.get(f"{tool_code}_include"):
        return None
    grade = form.get(f"{tool_code}_grade", "P")
    if grade == "B":
        return f"{tool_code}_B"
    addons = "".join(
        str(n) for n in sorted(CATALOG[tool_code].addons)
        if form.get(f"{tool_code}_addon_{n}")
    )
    return f"{tool_code}_P" + (f"_{addons}" if addons else "")


@app.route("/generate", methods=["GET", "POST"])
@require_password
def do_generate():
    if request.method == "GET":
        # 폼 제출 없이 GET으로 직접 들어온 경우 (예: 브라우저가 예전에 방문한
        # /generate 주소를 자동완성해 다시 연 경우) 405 대신 입력 화면으로 되돌린다.
        return redirect(url_for("index"))

    f = request.form
    university = (f.get("university") or "").strip()
    date_str = f.get("date") or ""
    try:
        issue_date = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else date.today()
    except ValueError:
        issue_date = date.today()

    formats = [x for x in ("hwp", "pdf") if f.get(f"fmt_{x}")] or ["hwp", "pdf"]

    codes = [c for c in (_code_for_tool("K", f), _code_for_tool("U", f)) if c]
    combine = f.get("combine", "together")

    errors: List[str] = []
    if not university:
        errors.append("대학명을 입력하세요.")
    if not codes:
        errors.append("설문도구를 하나 이상 선택하세요.")

    # 한 견적서 코드 목록 만들기 (together: 결합 / separate: 각각)
    quote_codes: List[str] = []
    if not errors:
        if len(codes) == 2 and combine == "together":
            quote_codes = ["+".join(codes)]
        else:
            quote_codes = codes

    results: List[Tuple[Quote, List[dict]]] = []
    if not errors:
        token = uuid.uuid4().hex
        out_dir = _DL_ROOT / token
        try:
            for code in quote_codes:
                quote = build_quote(university, code, issue_date)
                paths = generate(university, code, out_dir, issue_date, formats)
                files = [{"name": p.name,
                          "kind": p.suffix.lstrip(".").upper(),
                          "url": url_for("download", token=token, name=p.name)}
                         for p in paths]
                results.append((quote, files))
        except QuoteError as e:
            errors.append(str(e))
        except Exception as e:  # noqa: BLE001
            errors.append(f"생성 실패: {e}")

    if errors:
        return render_template(
            "index.html", tools=_tool_view(), today=issue_date.isoformat(),
            premier_price=f"{PREMIER_BASE_PRICE:,}", addon_price=f"{ADDON_PRICE:,}",
            errors=errors, form=f,
        ), 400

    return render_template("result.html", university=university,
                           issue_date=issue_date, results=results)


@app.route("/download/<token>/<name>")
@require_password
def download(token: str, name: str):
    # 토큰/파일명 검증 (경로 탈출 방지)
    if not token.isalnum():
        abort(404)
    folder = _DL_ROOT / token
    if not folder.is_dir() or not (folder / name).is_file():
        abort(404)
    return send_from_directory(folder, name, as_attachment=True)


@app.route("/healthz")
def healthz():
    return "ok"


def main():
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
