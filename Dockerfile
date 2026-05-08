FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

CMD ["celery", "-A", "app.celery_app", "worker", "-l", "info"]
