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
from typing import Dict, List, Tuple

from flask import (
    Flask, request, render_template, send_from_directory,
    Response, redirect, url_for, abort,
)

from .catalog import CATALOG, PREMIER_BASE_PRICE, ADDON_PRICE
from .documents import DOCUMENT_TYPES, extra_fields_for, coerce_extra
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


def _doc_type_view():
    """템플릿에 넘길 서류 종류 목록."""
    return [
        {"key": key, "label": doc.label, "supports_pdf": doc.supports_pdf,
         "has_extra": bool(doc.extra_fields)}
        for key, doc in DOCUMENT_TYPES.items()
    ]


def _all_extra_fields_view():
    """모든 서류의 추가 입력 항목을 key 기준으로 합쳐 반환한다(어떤 서류들이
    이 항목을 쓰는지도 함께).

    계약서·착수계·서약서처럼 여러 서류가 같은 extra 키(예: contract_date,
    period_start)를 공유하는 경우, 서류마다 입력칸을 따로 만들면 같은
    name 의 <input> 이 여러 개 생겨 폼 제출 시 값이 뒤섞인다(실제로 겪은
    버그 — 착수계만 체크해도 완료계용 숨은 입력칸이 같은 이름으로 남아있어
    제출값이 그쪽에서 읽혀 "값을 입력하세요" 오류가 계속 남). 그래서 입력칸은
    key 하나당 딱 하나만 만들고, 어느 서류 체크박스가 켜지면 보일지는
    'docs' 목록으로 표시해 프론트엔드에서 처리한다.
    """
    owners: Dict[str, List[str]] = {}
    fields: Dict[str, object] = {}
    for key, doc in DOCUMENT_TYPES.items():
        for f in doc.extra_fields:
            fields.setdefault(f.key, f)
            owners.setdefault(f.key, []).append(key)
    return [
        {"key": f.key, "label": f.label, "kind": f.kind, "required": f.required,
         "default": f.default, "help": f.help, "docs": owners[f.key]}
        for f in fields.values()
    ]


@app.route("/")
@require_password
def index():
    return render_template(
        "index.html",
        tools=_tool_view(),
        doc_types=_doc_type_view(),
        all_extra=_all_extra_fields_view(),
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
    doc_types = [d for d in DOCUMENT_TYPES if f.get(f"doc_{d}")] or ["quote"]

    codes = [c for c in (_code_for_tool("K", f), _code_for_tool("U", f)) if c]
    combine = f.get("combine", "together")

    errors: List[str] = []
    if not university:
        errors.append("대학명을 입력하세요.")
    if not codes:
        errors.append("설문도구를 하나 이상 선택하세요.")

    # 선택한 서류의 추가 입력값(계약 날짜 등) 수집·검증
    extra: dict = {}
    raw_extra = {ef.key: f.get(f"extra_{ef.key}", "")
                 for ef in extra_fields_for(doc_types)}
    try:
        extra = coerce_extra(doc_types, raw_extra)
    except ValueError as e:
        errors.append(str(e))

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
                generated = generate(university, code, out_dir, issue_date,
                                     formats, doc_types, extra=extra)
                files = [{"name": gf.path.name,
                          "kind": gf.path.suffix.lstrip(".").upper(),
                          "label": gf.doc_label,
                          "url": url_for("download", token=token, name=gf.path.name)}
                         for gf in generated]
                results.append((quote, files))
        except QuoteError as e:
            errors.append(str(e))
        except Exception as e:  # noqa: BLE001
            errors.append(f"생성 실패: {e}")

    if errors:
        return render_template(
            "index.html", tools=_tool_view(), doc_types=_doc_type_view(),
            all_extra=_all_extra_fields_view(),
            today=issue_date.isoformat(),
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
