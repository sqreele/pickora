from __future__ import annotations

import logging
import os
import subprocess
import time

import schedule

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

SYNC_TIME = os.getenv("SYNC_HOUR", "04:00")


def run_pipeline() -> None:
    logging.info("Starting Pickora feed pipeline")
    result = subprocess.run(
        ["python", "run_once.py"],
        check=False,
    )
    logging.info("Pipeline exited with code %s", result.returncode)


schedule.every().day.at(SYNC_TIME).do(run_pipeline)

logging.info("Scheduler started. Daily sync: %s", SYNC_TIME)

if not os.path.exists("/app/public/products.json"):
    run_pipeline()

while True:
    schedule.run_pending()
    time.sleep(30)
