"""
Prefect flows de Rent Radar.

ZonaProp y ArgenProp son rápidos y no tienen problemas de bloqueo, así que corren
en el flow principal con cadencia corta. MercadoLibre usa Playwright (lento, ~20-30
min cuando scrapea bien) y aplica bloqueos anti-bot si se lo golpea muy seguido, así
que corre en su propio flow con un intervalo mucho más largo e independiente. dbt
recoge la última corrida exitosa de cada fuente por separado (ver
silver/publicaciones.sql), así que no hace falta que coincidan en el tiempo.

Deploy (una sola vez, con el servidor y el pool ya creados):
    prefect work-pool create --type process local
    prefect deploy pipeline.py:pipeline --name cada_10min --pool local --interval 600
    prefect deploy pipeline.py:mercadolibre_pipeline --name meli_cada_1h --pool local --interval 3600

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
    """ZonaProp + ArgenProp, transformación, detección, notificación y dashboard."""
    futures = [
        task_ingest_zonaprop.submit(),
        task_ingest_argenprop.submit(),
    ]
    for f in futures:
        f.result()
    task_dbt()
    task_detect()
    task_notify()
    task_mapa()


@flow(name="rent-radar-mercadolibre", log_prints=True)
def mercadolibre_pipeline() -> None:
    """MercadoLibre por separado, con su propia cadencia (ver deploy arriba)."""
    task_ingest_mercadolibre()
