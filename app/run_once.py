from __future__ import annotations

import logging
import subprocess
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


def run(script: str) -> int:
    result = subprocess.run(["python", script], check=False)
    logging.info("%s exited with code %s", script, result.returncode)
    return result.returncode


def main() -> int:
    pipeline_code = run("run_pipeline.py")
    if pipeline_code != 0:
        return pipeline_code
    # Monitoring must never invalidate a successful product refresh.
    run("link_monitor.py")
    run("build_analytics_summary.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
