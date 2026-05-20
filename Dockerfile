FROM python:3.13-alpine

ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apk add --no-cache ca-certificates \
    && addgroup -S app \
    && adduser -S app -G app \
    && mkdir -p /data \
    && chown -R app:app /app /data

COPY --chown=app:app main.py /app/main.py

USER app

CMD ["python", "/app/main.py"]
