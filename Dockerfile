FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN addgroup --gid 10001 --system ivory && adduser --uid 10001 --system --ingroup ivory ivory \
    && mkdir -p /app/platform-storage /app/tenant-data \
    && chown -R ivory:ivory /app
USER ivory
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
