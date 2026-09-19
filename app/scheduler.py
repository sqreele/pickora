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

SYNC_INTERVAL_HOURS = max(1, int(os.getenv("SYNC_INTERVAL_HOURS", "2")))
GSC_COLLECTION_TIME = os.getenv("GSC_COLLECTION_TIME", "06:00")


def run_pipeline() -> None:
    logging.info("Starting Pickora feed pipeline")
    result = subprocess.run(
        ["python", "run_once.py"],
        check=False,
    )
    logging.info("Pipeline exited with code %s", result.returncode)


def run_search_console_collection() -> None:
    """Daily GSC is independent of, and must not delay, the two-hour feed job."""
    if not os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_B64", "").strip():
        logging.info("Skipping Search Console collection: credentials are not configured")
        return
    result = subprocess.run(["python", "search_console.py", "--days", "28"], check=False)
    logging.info("Search Console collection exited with code %s", result.returncode)


schedule.every(SYNC_INTERVAL_HOURS).hours.do(run_pipeline)
schedule.every().day.at(GSC_COLLECTION_TIME).do(run_search_console_collection)

logging.info(
    "Scheduler started. Sync interval: every %s hour(s)",
    SYNC_INTERVAL_HOURS,
)
logging.info("Search Console collection scheduled daily at %s", GSC_COLLECTION_TIME)

if not os.path.exists("/app/public/products.json"):
    run_pipeline()

while True:
    schedule.run_pending()
    time.sleep(30)
