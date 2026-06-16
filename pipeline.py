"""
Prefect flow principal de Rent Radar.

Deploy (una sola vez, con el servidor y el pool ya creados):
    prefect work-pool create --type process local
    prefect deploy pipeline.py:pipeline --name cada_35min --pool local --interval 2100

El worker levanta los runs; este archivo no necesita correr 24/7.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from prefect import flow, task, get_run_logger

ROOT = Path(__file__).parent


def _run(script: str, *args: str) -> None:
    logger = get_run_logger()
    proc = subprocess.Popen(
        [sys.executable, ROOT / script, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=ROOT,
    )
    for line in proc.stdout:
        logger.info(line.rstrip())
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"{script} falló con código {proc.returncode}")


@task(name="ingest-zonaprop")
def task_ingest_zonaprop() -> None:
    _run("run_ingest.py", "--source", "zonaprop")


@task(name="ingest-argenprop")
def task_ingest_argenprop() -> None:
    _run("run_ingest.py", "--source", "argenprop")


@task(name="ingest-mercadolibre")
def task_ingest_mercadolibre() -> None:
    _run("run_ingest.py", "--source", "mercadolibre")


@task(name="dbt")
def task_dbt() -> None:
    _run("run_dbt.py")


@task(name="detect-events")
def task_detect() -> None:
    _run("detect_events.py")


@task(name="notify")
def task_notify() -> None:
    _run("notify.py")


@task(name="dashboard")
def task_mapa() -> None:
    _run("run_dashboard.py")


@flow(name="rent-radar", log_prints=True)
def pipeline() -> None:
    futures = [
        task_ingest_zonaprop.submit(),
        task_ingest_argenprop.submit(),
        task_ingest_mercadolibre.submit(),
    ]
    for f in futures:
        f.result()
    task_dbt()
    task_detect()
    task_notify()
    task_mapa()
