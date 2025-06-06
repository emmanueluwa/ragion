from celery import Celery
import os
from dotenv import load_dotenv

load_dotenv()

redis_endpoint = os.environ.get("REDIS_ENDPOINT")
redis_port = os.environ.get("REDIS_PORT", "6379")
redis_password = os.environ.get("REDIS_PASSWORD")

redis_url = f"rediss://default:{redis_password}@{redis_endpoint}:{redis_port}?ssl_cert_reqs=CERT_NONE"

# Configure Celery
celery_app = Celery("my_flask_app", broker=redis_url, backend=redis_url)


# Celery configuration
celery_app.conf.update(
    result_expires=60,  # Results expire after 60 seconds
    redis_backend_use_ssl={"ssl_cert_reqs": None},
    broker_use_ssl={"ssl_cert_reqs": None},
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)

import tasks
