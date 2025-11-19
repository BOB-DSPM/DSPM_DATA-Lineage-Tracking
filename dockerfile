# Dockerfile - SageMaker Lineage API
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8300

# 운영 시 필요한 유틸(헬스체크 curl + git clone)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    tini \
    && rm -rf /var/lib/apt/lists/*

# 비루트 유저
RUN useradd -ms /bin/bash appuser
WORKDIR /app

# 의존성 (캐시 최적화)
COPY requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip && pip install --no-cache-dir -r /tmp/requirements.txt

# 앱 소스
COPY api.py lineage.py ./
COPY modules ./modules
COPY demo_repo ./demo_repo

# 권한
RUN chown -R appuser:appuser /app
USER appuser

# 헬스체크
EXPOSE 8300
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
  CMD curl -sf http://127.0.0.1:${PORT}/health || exit 1

# uvicorn 실행
ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["uvicorn","api:app","--host","0.0.0.0","--port","8300"]
