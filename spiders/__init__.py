from collections.abc import Callable, Iterable

from app.models import Listing
from .argenprop import scrape_argenprop
from .mercadolibre import scrape_mercadolibre
from .zonaprop import scrape_zonaprop


ScraperFunc = Callable[[Iterable[str], int], list[Listing]]

_SCRAPERS: dict[str, ScraperFunc] = {
	"mercadolibre": scrape_mercadolibre,
	"argenprop": scrape_argenprop,
	"zonaprop": scrape_zonaprop,
}


def scrape_source(source: str, start_urls: Iterable[str], max_pages: int = 3) -> list[Listing]:
	"""Ejecuta el scraper de la fuente pedida."""
	normalized = source.strip().lower()

	scraper_func = _SCRAPERS.get(normalized)
	if not scraper_func:
		supported = ", ".join(sorted(_SCRAPERS.keys()))
		raise ValueError(f"Fuente no soportada: '{source}'. Soportadas: [{supported}]")

	return scraper_func(start_urls, max_pages=max_pages)


__all__ = ["scrape_source"]
