FROM python:3.13-slim

# libgomp1: xgboost가 런타임에 요구하는 OpenMP 공유 라이브러리
# fonts-nanum: matplotlib figure·Playwright PDF 렌더링에 쓰이는 한글 폰트
#   (app/services/bias/bias_figures.py가 Linux에서 NanumGothic 계열을 찾음)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chromium을 고정 경로에 설치해 이후 non-root 유저로 전환해도 찾을 수 있게 함
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chromium

COPY app ./app

RUN useradd --create-home --shell /bin/false appuser \
    && mkdir -p /tmp/matplotlib-cache \
    && chown -R appuser:appuser /app /ms-playwright /tmp/matplotlib-cache

USER appuser

# matplotlib 폰트 캐시를 쓰기 가능한 경로로 고정 (기본값은 홈 디렉터리 하위라 권한 문제 소지)
ENV MPLCONFIGDIR=/tmp/matplotlib-cache

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
