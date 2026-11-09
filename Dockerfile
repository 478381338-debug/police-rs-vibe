FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    POLICE_INSTANCE_DIR=/app/instance

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --system --uid 10001 --create-home appuser

COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser crawler ./crawler
COPY --chown=appuser:appuser web ./web
COPY --chown=appuser:appuser data ./data
COPY --chown=appuser:appuser matlab ./matlab
COPY --chown=appuser:appuser arcgis ./arcgis

RUN mkdir -p /app/instance /app/logs /app/data/cache \
    && chown -R appuser:appuser /app/instance /app/logs /app/data/cache

USER appuser
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health/ready', timeout=4)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]