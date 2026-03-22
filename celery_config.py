from celery import Celery
from celery.schedules import crontab
import os
from dotenv import load_dotenv

load_dotenv()

redis_endpoint = os.environ.get("REDIS_ENDPOINT", "172.18.0.1")
redis_port = os.environ.get("REDIS_PORT", "6379")
redis_password = os.environ.get("REDIS_PASSWORD", "")

if redis_password:
    redis_url = f"redis://default:{redis_password}@{redis_endpoint}:{redis_port}/0"
else:
    redis_url = f"redis://{redis_endpoint}:{redis_port}/0"


# Configure Celery
celery_app = Celery("my_flask_app", broker=redis_url, backend=redis_url)


# Celery configuration
celery_app.conf.update(
    result_expires=3600,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "cleanup-stuck-deletions": {
            "task": "tasks.cleanup_stuck_deletions",
            "schedule": crontab(minute="*/30"),
        }
    },
)

import tasks
