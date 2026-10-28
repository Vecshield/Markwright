"""Background worker entrypoint (referenced by docker-compose `worker` service).

v1 scope: arq (Redis) in the provider profile, APScheduler on-prem, running
calibration jobs, batch verification and scheduled rotation. The POC runs
calibration synchronously in the API process, so this is an explicit stub
rather than a silent import error.
"""
from __future__ import annotations

import sys

if __name__ == "__main__":
    sys.exit(
        "markwright.jobs.worker is a v1 feature (see docs/poc-plan.md phase 4). "
        "The POC runs calibration synchronously in the API process; "
        "remove the `worker` service from your compose profile."
    )
