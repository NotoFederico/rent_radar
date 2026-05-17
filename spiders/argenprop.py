from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from app.models import Listing


@dataclass(slots=True)
class SpiderConfig:
    """Configuracion base para scraping de Argenprop."""

    request_timeout: int = 20
    max_pages: int = 3


class ArgenpropSpider:
    """Spider HTTP liviano para resultados de Argenprop."""

    def __init__(self, config: SpiderConfig | None = None):
        self.config = config or SpiderConfig()
        self.session = requests.Session()
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
            for detail_url, seed in self._iter_detail_urls(start_url):
                listing = self._parse_listing(detail_url, seed)
                if listing is not None:
                    results.append(listing)
        return results

    def _iter_detail_urls(self, start_url: str) -> Iterable[tuple[str, dict[str, float | str | None]]]:
        current_url = start_url
        current_page = 1
        for _ in range(self.config.max_pages):
            soup = self._get_soup(current_url)
            if soup is None:
                return

            cards = soup.select("div.listing__item")
            for card in cards:
                link = card.select_one("a.card")
                if link is None:
                    continue
                href = link.get("href")
                if not href:
                    continue

                full_url = urljoin(current_url, href)
                source_id = link.get("data-item-card") or full_url
                price_text = "".join(card.select_one("p.card__price").stripped_strings) if card.select_one("p.card__price") else None
                currency_span = card.select_one("span.card__currency")
                price, currency = self._clean_price_argenprop(
                    price_text,
                    currency_span.get_text(" ", strip=True) if currency_span else None,
                )
                seed_data: dict[str, float | str | None] = {
                    "listing_id": str(source_id),
                    "price": price,
                    "currency": currency,
                    "expenses": self._clean_expenses(card.select_one("span.card__expenses").get_text(" ", strip=True) if card.select_one("span.card__expenses") else None),
                    "location": self._build_location(card),
                }
                yield full_url, seed_data

            next_page_url = self._resolve_next_page_url(soup, current_url, current_page)
            if not next_page_url:
                return
            current_url = next_page_url
            current_page += 1

    @staticmethod
    def _resolve_next_page_url(soup: BeautifulSoup, current_url: str, current_page: int) -> str | None:
        next_selectors = (
            "li.pagination__page-next a",
            "a[rel='next']",
            "a[aria-label*='Siguiente']",
        )
        for selector in next_selectors:
            node = soup.select_one(selector)
            if node and node.get("href"):
                return urljoin(current_url, node["href"])

        expected_page = current_page + 1
        token = f"pagina-{expected_page}"

        # En Argenprop actual, la paginacion suele venir en data-link-href dentro de <span>.
        data_link_node = soup.select_one(f"[data-link-href*='{token}']")
        if data_link_node and data_link_node.get("data-link-href"):
            return urljoin(current_url, html.unescape(data_link_node["data-link-href"]))

        html_text = str(soup)
        pattern = re.compile(rf'(/[^"]*{re.escape(token)}[^"\s]*)', re.IGNORECASE)
        match = pattern.search(html_text)
        if not match:
            return None

        href = html.unescape(match.group(1))
        return urljoin(current_url, href)

    def _parse_listing(self, detail_url: str, seed: dict[str, float | str | None]) -> Listing | None:
        soup = self._get_soup(detail_url)
        if soup is None:
            return None

        summary_title = self._text(soup, "h2.title-type-sup-property")
        title = summary_title or self._text(soup, "h1") or "Propiedad en Argenprop"
        specifications = self._extract_specifications(soup)
        specs_text = " ".join(specifications)

        rooms = self._extract_numeric_from_text(summary_title, r"(\d+(?:[\.,]\d+)?)\s*amb")
        surface_m2 = self._extract_numeric_from_text(summary_title, r"(\d+(?:[\.,]\d+)?)\s*m")
        bedrooms = self._extract_numeric_from_text(summary_title, r"(\d+(?:[\.,]\d+)?)\s*dorm")
        bathrooms = self._extract_numeric_from_text(summary_title, r"(\d+(?:[\.,]\d+)?)\s*ba")

        if rooms is None or surface_m2 is None or bedrooms is None or bathrooms is None:
            rooms = rooms or self._extract_numeric_from_text(specs_text, r"(?:cant\.?\s*)?amb(?:ientes?)?\s*:?\s*(\d+(?:[\.,]\d+)?)")
            surface_m2 = surface_m2 or self._extract_numeric_from_text(specs_text, r"(?:sup\.?\s*(?:cubierta|total)?\s*:?\s*)?(\d+(?:[\.,]\d+)?)\s*(?:m2|m²)")
            bedrooms = bedrooms or self._extract_numeric_from_text(specs_text, r"(?:cant\.?\s*)?dorm(?:itorios?)?\s*:?\s*(\d+(?:[\.,]\d+)?)")
            bathrooms = bathrooms or self._extract_numeric_from_text(specs_text, r"(?:cant\.?\s*)?ba(?:n|ñ)os?\s*:?\s*(\d+(?:[\.,]\d+)?)")

        published_at = self._extract_published_date(soup)
        latitude, longitude = self._extract_coordinates(soup)
        seller = self._extract_seller(soup)

        listing_id = str(seed.get("listing_id") or detail_url)
        location = seed.get("location")
        return Listing(
            source="argenprop",
            listing_id=listing_id,
            url=detail_url,
            title=title,
            price=self._as_float(seed.get("price")),
            currency=self._as_str(seed.get("currency")),
            expenses=self._as_float(seed.get("expenses")),
            location=self._as_str(location),
            latitude=latitude,
            longitude=longitude,
            rooms=rooms,
            bedrooms=bedrooms,
            bathrooms=bathrooms,
            surface_m2=surface_m2,
            published_at=published_at,
            seller=seller,
            specifications=specifications,
        )

    def _get_soup(self, url: str) -> BeautifulSoup | None:
        try:
            response = self.session.get(url, timeout=self.config.request_timeout)
            response.raise_for_status()
        except requests.RequestException:
            return None
        return BeautifulSoup(response.text, "html.parser")

    @staticmethod
    def _text(soup: BeautifulSoup, selector: str) -> str | None:
        node = soup.select_one(selector)
        if node is None:
            return None
        text = node.get_text(" ", strip=True)
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
    def _clean_price_argenprop(full_text: str | None, currency_span: str | None) -> tuple[float | None, str | None]:
        if not full_text:
            return None, None

        text_clean = full_text.lower().strip()
        if "+" in text_clean:
            text_clean = text_clean.split("+", maxsplit=1)[0]
        if "exp" in text_clean:
            text_clean = text_clean.split("exp", maxsplit=1)[0]

        currency = "ARS"
        currency_span_normalized = (currency_span or "").upper()
        if "usd" in text_clean or "u$s" in text_clean or "USD" in currency_span_normalized:
            currency = "USD"

        clean_val = re.sub(r"[^\d]", "", text_clean)
        if not clean_val:
            return None, currency

        return float(clean_val), currency

    @staticmethod
    def _clean_expenses(text: str | None) -> float | None:
        if not text:
            return None
        clean_val = re.sub(r"[^\d]", "", text)
        if not clean_val:
            return None
        return float(clean_val)

    @staticmethod
    def _build_location(card: BeautifulSoup) -> str | None:
        address = card.select_one("p.card__address")
        zone = card.select_one("p.card__title--primary")
        address_text = address.get_text(" ", strip=True) if address else ""
        zone_text = zone.get_text(" ", strip=True) if zone else ""
        value = f"{address_text}, {zone_text}" if address_text and zone_text else (address_text or zone_text)
        return value or None

    @staticmethod
    def _extract_numeric_from_text(text: str | None, pattern: str) -> float | None:
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
    def _extract_specifications(soup: BeautifulSoup) -> list[str]:
        specs: list[str] = []

        for node in soup.select("ul.property-features li p"):
            text = " ".join(node.get_text(" ", strip=True).split())
            if text:
                specs.append(text)

        for node in soup.select("li.property-features-item"):
            text = " ".join(node.get_text(" ", strip=True).split())
            if text:
                specs.append(text)

        return specs

    @staticmethod
    def _extract_coordinates(soup: BeautifulSoup) -> tuple[float | None, float | None]:
        map_container = soup.select_one("div[data-location-map]")
        if map_container is not None:
            lat_str = map_container.get("data-latitude")
            lng_str = map_container.get("data-longitude")
            if lat_str and lng_str:
                try:
                    return float(lat_str.replace(",", ".")), float(lng_str.replace(",", "."))
                except ValueError:
                    pass

        html_text = str(soup)
        lat_match = re.search(r'data-latitude=["\'](-?\d+[\.,]\d+)["\']', html_text)
        lng_match = re.search(r'data-longitude=["\'](-?\d+[\.,]\d+)["\']', html_text)
        if lat_match and lng_match:
            try:
                return float(lat_match.group(1).replace(",", ".")), float(lng_match.group(1).replace(",", "."))
            except ValueError:
                return None, None

        return None, None

    @staticmethod
    def _extract_seller(soup: BeautifulSoup) -> str | None:
        for selector in (
            ".form-widget-details .form-details-heading",
            ".form-widget-footer .form-details-heading",
        ):
            node = soup.select_one(selector)
            if node is None:
                continue
            text = node.get_text(" ", strip=True)
            if text:
                return text
        return None

    @staticmethod
    def _extract_published_date(soup: BeautifulSoup) -> datetime | None:
        scripts = soup.select("script[type='application/ld+json']")
        for script in scripts:
            raw = script.string or script.get_text(strip=True)
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            items = payload if isinstance(payload, list) else [payload]
            for item in items:
                if not isinstance(item, dict):
                    continue
                value = item.get("uploadDate") or item.get("datePublished")
                if not isinstance(value, str):
                    continue
                parsed = ArgenpropSpider._parse_iso_datetime(value)
                if parsed:
                    return parsed
        return None

    @staticmethod
    def _parse_iso_datetime(value: str) -> datetime | None:
        normalized = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
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


def scrape_argenprop(start_urls: Iterable[str], max_pages: int = 3) -> list[Listing]:
    """Atajo funcional para obtener publicaciones de Argenprop."""
    spider = ArgenpropSpider(config=SpiderConfig(max_pages=max_pages))
    return spider.scrape(start_urls)
