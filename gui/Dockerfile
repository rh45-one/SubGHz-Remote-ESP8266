FROM python:3.12-slim
ARG APP_VERSION=dev
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_VERSION=${APP_VERSION}
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home app && mkdir /app/data && chown app:app /app/data
COPY --chown=app:app app ./app
LABEL org.opencontainers.image.title="SubGHz-Remote-ESP8266 GUI" \
      org.opencontainers.image.description="Homelab FastAPI and mobile Web UI for SubGHz-Remote-ESP8266" \
      org.opencontainers.image.source="https://github.com/rh45-one/SubGHz-Remote-ESP8266" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log", "--no-proxy-headers"]
