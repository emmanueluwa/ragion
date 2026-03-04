from celery import Celery
import os
from dotenv import load_dotenv

load_dotenv()


redis_url = f"redis://localhost:6379/0"

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
)

import tasks
