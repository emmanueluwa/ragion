FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
# RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && pip show sentence-transformers


COPY . .

EXPOSE 8080

# CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8080", "app:app"]

CMD ["celery", "-A", "celery_config", "worker", "--loglevel=info", "--queues=cloud_queue"]
