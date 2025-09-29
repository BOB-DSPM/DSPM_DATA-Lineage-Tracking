# DSPM PII Scanner - FastAPI
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 소스
COPY app.py .

# 런타임 포트 (정보용)
EXPOSE 8100

# uvicorn으로 FastAPI 기동
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
