import redis
import os
from dotenv import load_dotenv

load_dotenv()

# getting upstash redis credentials
redis_endpoint = os.environ.get("REDIS_ENDPOINT")
redis_port = int(os.environ.get("REDIS_PORT", 6379))
redis_password = os.environ.get("REDIS_PASSWORD")


try:
    r = redis.Redis(
        host=redis_endpoint,
        port=redis_port,
        password=redis_password,
        ssl=True,
        socket_timeout=5,
    )

    ping_result = r.ping()

    print(f"redis connection successful :)", {ping_result})

except Exception as e:
    print(f"redis connection failed :( {e}")
