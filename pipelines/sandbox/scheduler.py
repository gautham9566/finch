"""APScheduler IST daemon registering all Finch schedules from config/schedule.yaml."""

from __future__ import annotations

import signal
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from libs.common.config import REPO_ROOT, get_schedule_config
from libs.common.db import get_conn, init_schema
from libs.common.logging import get_logger

IST = ZoneInfo("Asia/Kolkata")
log = get_logger("finch.scheduler")


def _run_scheduled_command(job_name: str, command: str) -> None:
    started = datetime.now(IST)
    log.info(f"Starting scheduled job '{job_name}': {command}")
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=3600,
        )
        status = "success" if proc.returncode == 0 else "failed"
        stdout_tail = proc.stdout.strip()[-600:]
        combined_tail = (proc.stdout + "\n" + proc.stderr).strip()[-600:]
        summary = ((stdout_tail or combined_tail) if proc.returncode == 0 else combined_tail) or (
            f"exit_code={proc.returncode}"
        )
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        summary = str(exc)

    finished = datetime.now(IST)
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO job_runs (job_name, started_at_ist, finished_at_ist, duration_sec, status, summary)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (job_name, started, finished, round((finished - started).total_seconds(), 2), status, summary),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Failed to record job_run for {job_name}: {exc}")
    log.info(f"Finished scheduled job '{job_name}' -> {status}")


def main() -> None:
    init_schema()
    cfg = get_schedule_config()
    scheduler = BlockingScheduler(timezone=IST)

    for job in cfg.get("jobs", []):
        trigger = CronTrigger.from_crontab(job["cron"], timezone=IST)
        scheduler.add_job(
            _run_scheduled_command,
            trigger=trigger,
            args=[job["name"], job["command"]],
            id=job["name"],
            name=job["description"],
            replace_existing=True,
            misfire_grace_time=300,
        )
        log.info(f"Registered schedule {job['name']} ({job['cron']} IST)")

    def _shutdown(signum, _frame):
        log.info(f"Signal {signum} received, shutting down scheduler")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    scheduler.start()


if __name__ == "__main__":
    main()
