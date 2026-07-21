# 견적서 자동화 (성균관대학교 산학협력단)

대학에서 요청한 **신청등급 코드**(예: `K_P_12`, `U_B`)를 입력하면
원본 한글 양식 그대로의 **견적서를 HWP · PDF 로 자동 생성**합니다.

각 대학이 네이버 메일로 요청 → 노션에 신청등급 정리 → 이 도구로 등급코드를 넣어
견적서를 뽑는 흐름을 지원합니다. (현재 단계: **등급코드 입력 → 생성**)

---

## 1. 신청등급 코드 규칙

코드는 세 부분으로 구성됩니다: `<설문도구>_<등급>[_<부가서비스숫자들>]`

| 자리 | 값 | 의미 |
|------|-----|------|
| 1번째 | `K` / `U` | **K**-NSSE / **U**ICA (설문도구) |
| 2번째 | `B` / `P` | **B**ASIC(베이직) / **P**REMIER(프리미어) |
| 끝 숫자 | `1`,`2`,`12`… | 프리미어에 추가하는 부가서비스 번호 (도구별로 독립) |

### 설문도구·부가서비스 카탈로그

**K-NSSE (`K`)** — 학부교육의 질과 성과 진단 및 분석
- `1` 단과대학별 분석 및 보고서 제공
- `2` Peer Benchmarking 분석 및 보고서 제공

**UICA (`U`)** — 대학 혁신역량 진단 및 분석
- `1` Peer Benchmarking 분석 및 보고서 제공

### 가격 (부가세 포함)
- 프리미어 기본: **2,200,000원** (공급가 2,000,000 + VAT 200,000)
- 부가서비스 1개당: **1,100,000원** (공급가 1,000,000 + VAT 100,000)
- 베이직: **0원**

### 코드 예시
| 코드 | 뜻 | 합계(부가세 포함) |
|------|-----|------|
| `U_P` | UICA 프리미어 | 2,200,000 |
| `K_P_2` | K-NSSE 프리미어 + Peer Benchmarking | 3,300,000 |
| `K_P_12` | K-NSSE 프리미어 + 부가 2개 | 4,400,000 |
| `K_P_2+U_B` | (한 장에) K-NSSE 프리미어+Peer, UICA 베이직 | 3,300,000 |
| `K_P_12+U_P_1` | (한 장에) 두 도구 프리미어 모두 | 7,700,000 |

- **한 견적서에 두 도구**를 넣으려면 `+` 로 결합: `K_P_12+U_B`
- **두 도구지만 견적서를 따로 2장** 받으려면 코드를 각각 입력 (아래 CLI 참고)

---

## 2. 설치

```bash
cd quote_automation
pip install -e .        # olefile, reportlab 자동 설치
```

PDF 한글 렌더링에는 나눔고딕 폰트가 필요합니다.
```bash
sudo apt-get install -y fonts-nanum      # Ubuntu/Debian
```

---

## 3. 사용법 (CLI)

```bash
# 한 견적서 (K-NSSE 프리미어 + 부가1,2  와  UICA 베이직 을 한 장에)
quote-gen --univ 서원대학교 --code K_P_12+U_B

# 두 도구를 이용하되 견적서를 따로 2장
quote-gen --univ OO대학교 --code K_P_2 --code U_B

# 발급일자 지정 (기본은 오늘), PDF 만 생성
quote-gen --univ OO대학교 --code U_P --date 2026-07-21 --format pdf

# 사용 가능한 등급 코드 전체 보기
quote-gen --list
```

생성 파일: `output/견적서_<대학명>_<코드>_<날짜>.hwp` / `.pdf`

> `pip install` 없이 바로 쓰려면: `PYTHONPATH=src python -m quote_automation --univ ... --code ...`

### 파이썬 API

```python
from datetime import date
from quote_automation.generator import generate

paths = generate("서원대학교", "K_P_12+U_B",
                 out_dir="output", issue_date=date(2026, 7, 21),
                 formats=["hwp", "pdf"])
```

---

## 4. 구조

```
src/quote_automation/
  catalog.py      회사정보·설문도구·부가서비스·가격  ← 설정은 여기만 고치면 됨
  korean_num.py   금액 → 한글 표기 (예: 삼백삼십만 원정)
  engine.py       등급코드 파싱 → 견적 데이터(품목/합계 계산)
  pdf_writer.py   PDF 렌더링 (reportlab, 양식 재현)
  hwp_writer.py   HWP 생성 (원본 양식 편집)
  cfbf.py         HWP 컨테이너(OLE 복합문서) 리더/라이터
  generator.py    고수준 API (HWP+PDF 함께)
  cli.py          명령줄 인터페이스
  templates/quote_template.hwp   원본 견적서 양식
tests/            엔진·HWP 구조 검증 테스트
```

### 서비스/가격을 바꾸려면
`catalog.py` 의 `CATALOG` / `PREMIER_BASE_PRICE` / `ADDON_PRICE` 만 수정하면 됩니다.
새 부가서비스는 해당 도구의 `addons` dict 에 `번호: "서비스명"` 한 줄만 추가하면
등급코드(`K_P_3` 등)로 바로 인식됩니다.

---

## 5. HWP 생성 방식과 주의사항

한글(HWP 5.0)은 리눅스/CI 환경에서 직접 열어볼 수 없으므로, 이 도구는
**원본 양식 파일을 템플릿으로 삼아 최소한만 안전하게 편집**합니다.

- 품목 테이블의 데이터 행은 원본 행을 **그대로 보존**하고, 필요 없는 행만 삭제 후
  행 번호를 다시 매깁니다. (셀 병합이 없어 안전)
- 발급일자·대학명·합계금액 등 전역 항목만 순수 텍스트로 교체합니다.
- 회사 도장 이미지·문서 스타일 정보(DocInfo) 등은 원본 그대로 유지됩니다.
- 생성 직후 **표 구조 불변식(행 수·열 타일링)을 자동 검증**합니다.

> ⚠️ **처음 사용 시 생성된 .hwp 를 한글에서 한 번 열어 확인**해 주세요.
> 이 환경에서는 한글로 직접 열어볼 수 없어 구조 검증까지만 수행합니다.
> PDF 는 완전히 독립적으로 렌더링되어 바로 사용할 수 있습니다.
>
> 참고: 생성된 .hwp 를 한글에서 열면 파일 미리보기 텍스트(PrvText)가 저장 시
> 자동 갱신됩니다.

---

## 6. 다음 단계 (확장 아이디어)

현재는 "등급코드 입력 → 생성" 단계입니다. 이후 확장 방향:

1. **노션 연동** — 노션 DB에서 각 대학의 신청등급 태그를 읽어 일괄 생성.
   `generator.generate()` 를 대학·코드 목록으로 반복 호출하면 됩니다.
2. **네이버 메일 연동** — 요청 메일 파싱 → 노션 자동 정리.

---

## 7. 테스트

```bash
pip install pytest
pytest            # 엔진 계산(실제 견적 샘플과 대조) + HWP 구조 검증
```
