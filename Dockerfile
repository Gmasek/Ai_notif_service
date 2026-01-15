FROM python:3.11-slim
ENV OPENAI_API_KEY=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

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Default command (can be overridden in docker-compose)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
