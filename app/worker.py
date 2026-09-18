import time
from datetime import datetime, timezone

from redis import Redis
from sqlalchemy import delete

from .config import get_settings
from .database import SessionLocal
from .models import UserSession


def run() -> None:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    while True:
        now = datetime.now(timezone.utc)
        try:
            redis.set("ivory-booking:worker-heartbeat", now.isoformat(), ex=180)
            with SessionLocal() as db:
                db.execute(delete(UserSession).where(UserSession.expires_at < now))
                db.commit()
        except Exception:
            # Docker restarts unhealthy dependencies; the worker retries without
            # changing or discarding tenant work.
            pass
        time.sleep(60)


if __name__ == "__main__":
    run()

