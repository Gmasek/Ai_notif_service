FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY guard.py scheduler.py ./

# Backup volume mount point
RUN mkdir -p /data
VOLUME ["/data"]

CMD ["python", "scheduler.py"]
