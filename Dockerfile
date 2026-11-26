# Standalone image of the router (Docker / docker compose).
# The router itself only needs the Python standard library; PyYAML is added
# to read YAML configuration files.
FROM python:3.12-alpine

RUN pip install --no-cache-dir "PyYAML>=6.0,<7" \
    && adduser -D -H -s /sbin/nologin router

COPY family_router/app /app

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER router
ENTRYPOINT ["python3", "-m", "family_router"]
CMD ["--config", "/config/config.yaml"]
