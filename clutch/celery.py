from celery import Celery
from celery.schedules import crontab

app = Celery("clutch")
app.conf.broker_url = "redis://127.0.0.1:6379/0"
app.conf.result_backend = "redis://127.0.0.1:6379/0"

# Configure periodic tasks
app.conf.beat_schedule = {
    "crawl-banlist-every-hour": {
        "task": "clutch.tasks.crawl_banlist",
        "schedule": crontab(minute=0),  # Run at the start of every hour
    },
}

app.autodiscover_tasks(["clutch"])


if __name__ == "__main__":
    app.start()
