from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime
from importlib.metadata import version

from app.db import ScraperDB
from app.models import Event, EventType, Listing, Notification, PipelineRun
from spiders import scrape_source

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)


def run_ingest(source: str, start_urls: list[str], max_pages: int) -> None:
	"""Ejecuta scraping de una fuente y persiste snapshots en Neon Postgres."""
	
	db = ScraperDB()
	run = PipelineRun(source=source, status="running", listings_found=0)

	try:
		db.insert_run(run.to_run_row())
		listings = scrape_source(source=source, start_urls=start_urls, max_pages=max_pages)
		snapshot_rows = [item.to_snapshot_row(run.run_id) for item in listings]
		db.insert_snapshots(snapshot_rows)

		run.listings_found = len(snapshot_rows)
		valid_count = sum(1 for r in snapshot_rows if r.get("precio") is not None)
		run.status = "ok" if valid_count > 0 else "empty"
		logging.info(
			"Ingest %s | fuente=%s | publicaciones=%d (con precio=%d)",
			run.status.upper(), source, run.listings_found, valid_count,
		)
	except Exception:
		run.status = "error"
		raise
	finally:
		run.finished_at = datetime.now(UTC)
		db.insert_run(run.to_run_row())
		db.close()


def parse_args() -> argparse.Namespace:
	"""Define argumentos de ejecucion."""
	parser = argparse.ArgumentParser(description="Rent Radar CLI")

	parser.add_argument(
		"--ingest",
		action="store_true",
		help="Ejecuta un scrape real e ingesta snapshots en Neon",
	)
	parser.add_argument(
		"--source",
		help="Fuente a ejecutar: mercadolibre | argenprop | zonaprop",
	)
	parser.add_argument(
		"--start-url",
		dest="start_urls",
		action="append",
		help="URL inicial del scrape (se puede repetir)",
	)
	parser.add_argument(
		"--max-pages",
		type=int,
		default=1,
		help="Cantidad maxima de paginas por URL inicial",
	)

	return parser.parse_args()


def main() -> None:
	"""Punto de entrada local."""
	args = parse_args()

	if args.ingest:
		if not args.source:
			raise ValueError("Falta --source para ejecutar --ingest")
		if not args.start_urls:
			raise ValueError("Falta al menos un --start-url para ejecutar --ingest")
		run_ingest(
			source=args.source,
			start_urls=args.start_urls,
			max_pages=args.max_pages,

		)
		return

	print("No se indico ninguna accion. Usa --ingest.")


if __name__ == "__main__":
	logging.info("Rent Radar v%s", version("rent-radar"))
	main()
