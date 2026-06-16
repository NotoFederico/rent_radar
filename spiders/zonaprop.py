from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from urllib.parse import urljoin

from curl_cffi import requests as curl_requests
from bs4 import BeautifulSoup

from app.models import Listing

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SpiderConfig:
    """Configuracion base para scraping de Zonaprop."""

    request_timeout: int = 20
    max_pages: int = 3


class ZonapropSpider:
    """Spider HTTP liviano para resultados de Zonaprop."""

    def __init__(self, config: SpiderConfig | None = None):
        self.config = config or SpiderConfig()
        self.session = curl_requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
            }
        )

    def scrape(self, start_urls: Iterable[str]) -> list[Listing]:
        results: list[Listing] = []
        for start_url in start_urls:
            logger.info("Iniciando scrape | url=%s", start_url)
            before = len(results)
            for detail_url, seed in self._iter_detail_urls(start_url):
                listing = self._parse_listing(detail_url, seed)
                if listing is not None:
                    results.append(listing)
                    print(f"\r  zonaprop: {len(results)} publicaciones...", end="", flush=True)
            logger.info("Scrape completado | url=%s | nuevas=%d", start_url, len(results) - before)
        print()
        if not results:
            logger.warning("Se obtuvieron 0 publicaciones en total. Revisar selectores o bloqueo HTTP.")
        return results

    def _iter_detail_urls(self, start_url: str) -> Iterable[tuple[str, dict[str, float | str | None]]]:
        current_url = start_url
        current_page = 1
        for _ in range(self.config.max_pages):
            soup = self._get_soup(current_url)
            if soup is None:
                logger.warning("No se pudo obtener pagina %d", current_page)
                return

            cards = soup.select("div[data-qa='posting PROPERTY']")
            if not cards:
                logger.warning("Pagina %d: ningun selector matcheo publicaciones", current_page)
            logger.info("Pagina %d: %d publicaciones encontradas", current_page, len(cards))
            for card in cards:
                href = card.get("data-to-posting")
                if not href:
                    continue
                full_url = urljoin(current_url, href)

                price_text = self._text(card, "[data-qa='POSTING_CARD_PRICE']")
                location = self._build_location(card)
                listing_id = card.get("data-id") or full_url

                seed_data: dict[str, float | str | None] = {
                    "listing_id": str(listing_id),
                    "price": self._parse_numeric(price_text),
                    "currency": self._parse_currency(price_text),
                    "expenses": self._parse_numeric(self._text(card, "[data-qa='expensas']")),
                    "location": location,
                    "summary": self._text(card, "[data-qa='POSTING_CARD_FEATURES']"),
                }
                yield full_url, seed_data

            next_url = self._resolve_next_page_url(soup, current_url, current_page)
            if not next_url:
                logger.debug("Sin pagina siguiente, finalizando en pagina %d.", current_page)
                return
            current_url = next_url
            current_page += 1

    @staticmethod
    def _resolve_next_page_url(soup: BeautifulSoup, current_url: str, current_page: int) -> str | None:
        # Zonaprop suele renderizar links de paginacion como "-pagina-2.html"
        # pero no siempre deja un boton "next" con selector estable.
        next_selectors = (
            "a[data-qa='PAGINATION_NEXT']",
            "a.paginationNext",
            "li.pagination__page-next a",
            "a[aria-label='Siguiente']",
            "a[rel='next']",
        )
        for selector in next_selectors:
            node = soup.select_one(selector)
            if node and node.get("href"):
                return urljoin(current_url, node["href"])

        expected_page = current_page + 1
        page_token = f"-pagina-{expected_page}.html"
        for node in soup.select("a[href]"):
            href = node.get("href")
            if href and page_token in href:
                return urljoin(current_url, href)

        return None

    def _parse_listing(self, detail_url: str, seed: dict[str, float | str | None]) -> Listing | None:
        soup = self._get_soup(detail_url)
        if soup is None:
            return None

        title = self._text(soup, "h1") or self._build_title(seed)
        if not title:
            return None

        summary_text = self._text(soup, "ul.section-icon-features") or self._as_str(seed.get("summary"))
        published_at = self._extract_publish_datetime(soup)
        latitude, longitude = self._extract_coordinates(soup)
        seller = self._extract_seller(soup)
        specifications = self._extract_specifications(soup)

        return Listing(
            source="zonaprop",
            listing_id=str(seed.get("listing_id") or detail_url),
            url=detail_url,
            title=title,
            price=self._as_float(seed.get("price")),
            currency=self._as_str(seed.get("currency")),
            expenses=self._as_float(seed.get("expenses")),
            location=self._as_str(seed.get("location")),
            latitude=latitude,
            longitude=longitude,
            rooms=self._extract_numeric(summary_text, r"(\d+(?:[\.,]\d+)?)\s*amb"),
            bedrooms=self._extract_numeric(summary_text, r"(\d+(?:[\.,]\d+)?)\s*dorm"),
            bathrooms=self._extract_numeric(summary_text, r"(\d+(?:[\.,]\d+)?)\s*ba"),
            surface_m2=self._extract_numeric(summary_text, r"(\d+(?:[\.,]\d+)?)\s*m"),
            published_at=published_at,
            seller=seller,
            specifications=specifications,
        )

    def _get_soup(self, url: str) -> BeautifulSoup | None:
        try:
            response = self.session.get(
                url,
                timeout=self.config.request_timeout,
                impersonate="chrome",
            )
            response.raise_for_status()
        except Exception as exc:
            logger.error("Error al pedir %s: %s", url, exc)
            return None
        logger.debug("HTTP %d | url=%s", response.status_code, url)
        return BeautifulSoup(response.text, "html.parser")

    @staticmethod
    def _text(node: BeautifulSoup, selector: str) -> str | None:
        value = node.select_one(selector)
        if value is None:
            return None
        text = value.get_text(" ", strip=True)
        return text or None

    @staticmethod
    def _parse_numeric(text: str | None) -> float | None:
        if not text:
            return None
        digits = re.sub(r"[^\d]", "", text)
        if not digits:
            return None
        return float(digits)

    @staticmethod
    def _parse_currency(text: str | None) -> str | None:
        if not text:
            return None
        normalized = text.upper()
        if "USD" in normalized or "U$S" in normalized:
            return "USD"
        if "$" in normalized:
            return "ARS"
        return None

    @staticmethod
    def _build_location(card: BeautifulSoup) -> str | None:
        address = ZonapropSpider._text(card, ".postingLocations-module__location-address")
        neighborhood = ZonapropSpider._text(card, "[data-qa='POSTING_CARD_LOCATION']")
        value = f"{address}, {neighborhood}" if address and neighborhood else (address or neighborhood)
        return value or None

    @staticmethod
    def _build_title(seed: dict[str, float | str | None]) -> str | None:
        location = seed.get("location")
        if isinstance(location, str) and location:
            return f"Propiedad en {location}"
        return None

    @staticmethod
    def _extract_seller(soup: BeautifulSoup) -> str | None:
        scripts_text = "\n".join(script.get_text(" ", strip=True) for script in soup.select("script"))
        match = re.search(r"['\"]publisher['\"]\s*:\s*\{[^}]*['\"]name['\"]\s*:\s*['\"]([^'\"]+)['\"]", scripts_text)
        return match.group(1).strip() if match else None

    @staticmethod
    def _extract_specifications(soup: BeautifulSoup) -> list[str]:
        specs: list[str] = []
        for li in soup.select("ul.section-icon-features li.icon-feature"):
            text = " ".join(li.get_text(" ", strip=True).split())
            if text:
                specs.append(text)
        return specs

    @staticmethod
    def _extract_publish_datetime(soup: BeautifulSoup) -> datetime | None:
        scripts_text = "\n".join(script.get_text(" ", strip=True) for script in soup.select("script"))
        match = re.search(r"publicationDateFormatted['\"]?\s*:\s*['\"]([^'\"]+)['\"]", scripts_text)
        if not match:
            return None
        value = match.group(1).replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _extract_coordinates(soup: BeautifulSoup) -> tuple[float | None, float | None]:
        html = str(soup)
        latitude, longitude = ZonapropSpider._extract_coordinates_js(html)
        if latitude is not None and longitude is not None:
            return latitude, longitude

        static_map_src = ZonapropSpider._extract_static_map_src(soup)
        if static_map_src:
            map_latitude, map_longitude = ZonapropSpider._extract_coordinates_static_map(static_map_src)
            if map_latitude is not None and map_longitude is not None:
                return map_latitude, map_longitude

        return latitude, longitude

    @staticmethod
    def _extract_coordinates_js(html: str) -> tuple[float | None, float | None]:
        lat_match = re.search(r'mapLatOf\s*=\s*"([^\"]+)"', html)
        lng_match = re.search(r'mapLngOf\s*=\s*"([^\"]+)"', html)

        latitude: float | None = None
        longitude: float | None = None

        try:
            if lat_match:
                latitude = float(base64.b64decode(lat_match.group(1)).decode("utf-8"))
            if lng_match:
                longitude = float(base64.b64decode(lng_match.group(1)).decode("utf-8"))
        except (TypeError, ValueError):
            return None, None

        return latitude, longitude

    @staticmethod
    def _extract_static_map_src(soup: BeautifulSoup) -> str | None:
        selectors = (
            "#static-map",
            "div.article-map img",
            "img[src*='maps.googleapis']",
            "img[src*='center=']",
        )
        for selector in selectors:
            node = soup.select_one(selector)
            if node is None:
                continue
            src = node.get("src")
            if src:
                return src
        return None

    @staticmethod
    def _extract_coordinates_static_map(src: str) -> tuple[float | None, float | None]:
        match = re.search(r"center=(-?\d+\.?\d*)%2C(-?\d+\.?\d*)", src)
        if not match:
            match = re.search(r"center=(-?\d+\.?\d*),(-?\d+\.?\d*)", src)
        if not match:
            return None, None

        try:
            latitude = float(match.group(1))
            longitude = float(match.group(2))
            return latitude, longitude
        except ValueError:
            return None, None

    @staticmethod
    def _extract_numeric(text: str | None, pattern: str) -> float | None:
        if not text:
            return None
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            return None
        value = match.group(1).replace(".", "").replace(",", ".")
        try:
            return float(value)
        except ValueError:
            return None

    @staticmethod
    def _as_float(value: float | str | None) -> float | None:
        if value is None:
            return None
        if isinstance(value, float):
            return value
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_str(value: float | str | None) -> str | None:
        if isinstance(value, str):
            return value
        return None


def scrape_zonaprop(start_urls: Iterable[str], max_pages: int = 3) -> list[Listing]:
    """Atajo funcional para obtener publicaciones de Zonaprop."""
    spider = ZonapropSpider(config=SpiderConfig(max_pages=max_pages))
    return spider.scrape(start_urls)
