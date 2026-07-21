# 견적서 웹 대시보드 배포용 이미지
# - 한글 폰트(나눔고딕) 포함 → PDF 한글 렌더링
# - gunicorn 으로 Flask 앱 서빙
FROM python:3.11-slim

# 한글 폰트 설치 (PDF 렌더링에 필요)
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-nanum \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir ".[web]"

ENV PORT=8000
EXPOSE 8000

# 워커 2개. APP_PASSWORD 환경변수로 공용 비밀번호 지정.
CMD ["sh", "-c", "gunicorn -b 0.0.0.0:${PORT:-8000} -w 2 --timeout 120 quote_automation.webapp:app"]
