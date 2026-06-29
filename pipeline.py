"""
Prefect flows de Rent Radar.

ZonaProp y ArgenProp son rápidos y no tienen problemas de bloqueo, así que corren
en el flow principal con cadencia corta. MercadoLibre usa Playwright (lento, ~20-30
min cuando scrapea bien) y aplica bloqueos anti-bot si se lo golpea muy seguido, así
que corre en su propio flow con un intervalo mucho más largo e independiente. dbt
recoge la última corrida exitosa de cada fuente por separado (ver
silver/publicaciones.sql), así que no hace falta que coincidan en el tiempo.

La limpieza de raw.snapshots (prune_pipeline) corre aparte de los dos ingests:
no depende de la salud de ninguno de los dos, y una vez por día alcanza de
sobra (ver prune_snapshots.py).

Deploy (una sola vez, con el servidor y el pool ya creados):
    prefect work-pool create --type process local
    prefect deploy pipeline.py:pipeline --name cada_10min --pool local --interval 600
    prefect deploy pipeline.py:mercadolibre_pipeline --name meli_cada_1h --pool local --interval 3600
    prefect deploy pipeline.py:prune_pipeline --name limpieza_diaria --pool local --cron "0 8 * * *"

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


@task(name="prune-snapshots")
def task_prune_snapshots() -> None:
    _run("prune_snapshots.py")


@task(name="dbt")
def task_dbt() -> None:
    _run("run_dbt.py")


@task(name="geocode-fallback")
def task_geocode_fallback() -> None:
    _run("geocode_fallback.py")


@task(name="detect-events")
def task_detect() -> None:
    _run("detect_events.py")


@task(name="notify")
def task_notify() -> None:
    _run("notify.py")


@task(name="dashboard")
def task_mapa() -> None:
    _run("run_dashboard.py")


@task(name="check-health")
def task_check_health() -> None:
    _run("check_health.py")


@flow(name="rent-radar", log_prints=True)
def pipeline() -> None:
    """ZonaProp + ArgenProp, transformación, detección, notificación y dashboard.

    Un ingest que falla (ej. corte de DNS hacia Neon) no debe frenar el resto del
    pipeline: check_health es justamente el paso que avisa de este tipo de
    cortes, así que tiene que poder correr en el mismo ciclo aunque el ingest
    de esa corrida no haya andado.
    """
    futures = [
        task_ingest_zonaprop.submit(),
        task_ingest_argenprop.submit(),
    ]
    for f in futures:
        error = f.result(raise_on_failure=False)
        if isinstance(error, Exception):
            print(f"Ingest falló, se sigue con el resto del pipeline igual: {error}")
    task_dbt()
    task_geocode_fallback()
    task_detect()
    task_notify()
    task_mapa()
    task_check_health()


@flow(name="rent-radar-mercadolibre", log_prints=True)
def mercadolibre_pipeline() -> None:
    """MercadoLibre por separado, con su propia cadencia (ver deploy arriba)."""
    task_ingest_mercadolibre()


@flow(name="rent-radar-prune", log_prints=True)
def prune_pipeline() -> None:
    """Mantenimiento de raw.snapshots, independiente de ambos ingests (ver deploy arriba).

    Ni el flow de 10 min ni el de MercadoLibre son el lugar correcto: no hace
    falta podar la tabla más de una vez por día, y la limpieza no debe
    depender de la salud de ningún ingest en particular.
    """
    task_prune_snapshots()
