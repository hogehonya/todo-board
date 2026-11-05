FROM python:3.13-slim

WORKDIR /app

ENV TZ=Asia/Tokyo
ENV PYTHONUNBUFFERED=1

RUN groupadd -g 1000 appuser && useradd -u 1000 -g appuser --no-log-init -s /usr/sbin/nologin appuser
RUN pip install --no-cache-dir fastapi uvicorn httpx

COPY app.py ./
COPY static/ static/
RUN chown -R appuser:appuser /app

USER appuser
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
