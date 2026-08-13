FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app/dashboard/

RUN chmod +x /app/dashboard/docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/app/dashboard/docker-entrypoint.sh"]
