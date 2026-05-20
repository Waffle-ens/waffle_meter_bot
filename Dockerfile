FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --shell /usr/sbin/nologin --create-home app \
    && mkdir -p /data \
    && chown -R app:app /app /data

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY --chown=app:app main.py /app/main.py

USER app

CMD ["python", "/app/main.py"]
