import os
import subprocess
import sys
from multiprocessing import Process


def run_worker():
    subprocess.call(["celery", "-A", "clutch.celery", "worker", "--loglevel=info"])


def run_beat():
    subprocess.call(["celery", "-A", "clutch.celery", "beat", "--loglevel=info"])


def run_flower():
    # Get port from environment variable or use default
    port = os.getenv("FLOWER_PORT", "5555")
    # Get basic auth credentials from environment variables
    basic_auth = os.getenv("FLOWER_BASIC_AUTH")
    auth_args = ["--basic_auth=" + basic_auth] if basic_auth else []

    subprocess.call(
        [
            "celery",
            "-A",
            "clutch.celery",
            "flower",
            f"--port={port}",
            *auth_args,
            "--loglevel=info",
        ]
    )


if __name__ == "__main__":
    # Add the parent directory to the Python path
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # Start worker, beat, and flower in separate processes
    worker_process = Process(target=run_worker)
    beat_process = Process(target=run_beat)
    flower_process = Process(target=run_flower)

    worker_process.start()
    beat_process.start()
    flower_process.start()

    try:
        worker_process.join()
        beat_process.join()
        flower_process.join()
    except KeyboardInterrupt:
        worker_process.terminate()
        beat_process.terminate()
        flower_process.terminate()
        worker_process.join()
        beat_process.join()
        flower_process.join()
