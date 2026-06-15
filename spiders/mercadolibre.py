from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from app.models import Listing

logger = logging.getLogger(__name__)

MELI_LISTING_ID_PATTERN = re.compile(r"MLA-(\d+)")

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass(slots=True)
class SpiderConfig:
    request_timeout: int = 30
    max_pages: int = 3
    delay_min: float = 5.0
    delay_max: float = 12.0
    session_size: int = 20       # reiniciar contexto del browser cada N listings de detalle
    session_restart_delay: float = 30.0  # pausa (seg) antes de abrir nuevo contexto


class MercadoLibreSpider:
    """Spider con Playwright para MercadoLibre (requiere JS para bypassear bot detection)."""

    def __init__(self, config: SpiderConfig | None = None):
        self.config = config or SpiderConfig()
        self._detail_count = 0
        logger.info("Iniciando navegador (Playwright/Chromium)...")
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._ctx = self._browser.new_context(
            user_agent=_UA,
            locale="es-AR",
            viewport={"width": 1366, "height": 768},
        )
        self._page = self._ctx.new_page()
        Stealth().apply_stealth_sync(self._page)
        logger.info("Navegador listo")

    def close(self) -> None:
        try:
            self._page.close()
            self._ctx.close()
            self._browser.close()
            self._pw.stop()
        except Exception:
            pass

    def __enter__(self) -> MercadoLibreSpider:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def scrape(self, start_urls: Iterable[str]) -> list[Listing]:
        results: list[Listing] = []
        for start_url in start_urls:
            logger.info("Iniciando scrape | url=%s", start_url)
            before = len(results)
            for detail_url in self._iter_detail_urls(start_url):
                listing = self._parse_listing(detail_url)
                if listing is not None:
                    results.append(listing)
                    print(f"\r  mercadolibre: {len(results)} publicaciones...", end="", flush=True)
            logger.info("Scrape completado | url=%s | nuevas=%d", start_url, len(results) - before)
        print()
        if not results:
            logger.warning("Se obtuvieron 0 publicaciones en total. Revisar selectores o bloqueo HTTP.")
        return results

    def _iter_detail_urls(self, start_url: str) -> Iterable[str]:
        all_urls: list[str] = []
        soup = self._navigate_search(start_url)
        for page_num in range(1, self.config.max_pages + 1):
            if soup is None:
                logger.warning("No se pudo obtener pagina %d", page_num)
                break

            seen_links: set[str] = set()
            for node in soup.select("a.poly-component__title"):
                href = node.get("href")
                if href and href not in seen_links:
                    seen_links.add(href)
                    all_urls.append(href)

            if not seen_links:
                page_title = soup.find("title")
                snippet = soup.get_text(" ", strip=True)[:300]
                logger.warning(
                    "Pagina %d: ningun selector matcheo | page_title=%s | snippet=%s",
                    page_num,
                    page_title.get_text() if page_title else "N/A",
                    snippet,
                )
                break
            logger.info("Pagina %d: %d publicaciones encontradas", page_num, len(seen_links))

            if page_num >= self.config.max_pages:
                break
            next_btn = soup.select_one("[data-andes-pagination-control='next']")
            if not next_btn or next_btn.get("data-andes-state") == "disabled":
                logger.debug("Sin pagina siguiente, finalizando en pagina %d.", page_num)
                break

            soup = self._click_next_page(page_num)

        return all_urls

    def _click_next_page(self, page_num: int) -> BeautifulSoup | None:
        self._delay()
        try:
            self._page.click("[data-andes-pagination-control='next']")
            try:
                self._page.wait_for_selector("a.poly-component__title", timeout=15_000)
            except Exception:
                self._page.wait_for_timeout(3_000)
            logger.info("Paginacion: click en Siguiente desde pagina %d | nueva url=%s", page_num, self._page.url)
            return BeautifulSoup(self._page.content(), "html.parser")
        except Exception as exc:
            logger.error("Error al navegar a pagina siguiente desde pagina %d: %s", page_num, exc)
            return None

    def _parse_listing(self, detail_url: str) -> Listing | None:
        soup = self._navigate(detail_url)
        if soup is None:
            return None

        title = (
            self._text(soup, "h1.ui-pdp-title")
            or self._text(soup, "h1.ui-vip-title")
            or self._text(soup, "h1[class*='title']")
            or self._text(soup, "h1")
        )
        if not title:
            h1_tags = [str(tag) for tag in soup.find_all("h1")]
            logger.warning(
                "Listing descartado: no se encontro titulo | url=%s | h1_tags=%s",
                detail_url,
                h1_tags[:3],
            )
            return None
        if not self._text(soup, "h1.ui-pdp-title"):
            logger.debug("Titulo encontrado con selector alternativo | url=%s | title=%s", detail_url, title)

        price = self._parse_price(
            self._text(soup, "span[data-testid='price-part'] span.andes-money-amount__fraction")
        )
        currency = self._parse_currency(
            self._text(soup, "span[data-testid='price-part'] span.andes-money-amount__currency-symbol")
        )

        lat, lon = self._extract_coordinates(soup)
        rooms = self._extract_numeric_feature(soup, r"(\d+(?:[\.,]\d+)?)\s*amb") or self._extract_from_specs_table(soup, "ambiente")

        return Listing(
            source="mercadolibre",
            listing_id=self._extract_listing_id(detail_url),
            url=detail_url,
            title=title,
            price=price,
            currency=currency,
            expenses=self._extract_expenses(soup),
            location=self._extract_location(soup),
            latitude=lat,
            longitude=lon,
            rooms=rooms,
            bedrooms=self._extract_numeric_feature(soup, r"(\d+(?:[\.,]\d+)?)\s*dorm"),
            bathrooms=self._extract_numeric_feature(soup, r"(\d+(?:[\.,]\d+)?)\s*ba"),
            surface_m2=self._extract_numeric_feature(soup, r"(\d+(?:[\.,]\d+)?)\s*m²"),
            published_at=self._extract_published_datetime(soup),
            seller=self._extract_seller(soup),
            specifications=self._extract_specifications(soup),
        )

    def _restart_context(self) -> None:
        """Cierra el contexto actual y abre uno nuevo para resetear cookies y fingerprint."""
        session_num = self._detail_count // self.config.session_size
        logger.info(
            "Rotando contexto del browser (sesion %d) — pausa de %.0fs...",
            session_num,
            self.config.session_restart_delay,
        )
        time.sleep(self.config.session_restart_delay)
        try:
            self._page.close()
            self._ctx.close()
        except Exception:
            pass
        self._ctx = self._browser.new_context(
            user_agent=_UA,
            locale="es-AR",
            viewport={"width": random.randint(1280, 1440), "height": random.randint(700, 900)},
        )
        self._page = self._ctx.new_page()
        Stealth().apply_stealth_sync(self._page)
        logger.info("Nuevo contexto listo")

    @staticmethod
    def _is_blocked(soup: BeautifulSoup) -> bool:
        """Detecta si MercadoLibre devolvio una pagina de captcha o verificacion."""
        title_tag = soup.find("title")
        if title_tag:
            t = title_tag.get_text("", strip=True).lower()
            if any(kw in t for kw in ("captcha", "verificaci", "robot", "acceso denegado", "error")):
                return True
        # Paginas de bloqueo tienen muy pocos elementos
        if len(soup.find_all(["h1", "h2", "p"])) < 3:
            return True
        return False

    def _delay(self) -> None:
        time.sleep(random.uniform(self.config.delay_min, self.config.delay_max))

    def _navigate_search(self, url: str) -> BeautifulSoup | None:
        """Navega a una página de resultados y espera que React renderice los cards."""
        logger.info("Esperando delay anti-bot antes de cargar busqueda...")
        self._delay()
        logger.info("Cargando pagina de busqueda...")
        try:
            resp = self._page.goto(url, timeout=self.config.request_timeout * 1000, wait_until="domcontentloaded")
            if resp and resp.status >= 400:
                logger.error("HTTP %d al pedir %s", resp.status, url)
                return None
            logger.info("Pagina cargada (HTTP %d), esperando publicaciones...", resp.status if resp else 0)
            try:
                self._page.wait_for_selector("a.poly-component__title", timeout=15_000)
            except Exception:
                logger.debug("wait_for_selector timeout, usando espera fija")
                self._page.wait_for_timeout(3_000)
            return BeautifulSoup(self._page.content(), "html.parser")
        except Exception as exc:
            logger.error("Error navegando a %s: %s", url, exc)
            return None

    def _navigate(self, url: str) -> BeautifulSoup | None:
        self._detail_count += 1
        if self._detail_count > 1 and (self._detail_count - 1) % self.config.session_size == 0:
            self._restart_context()

        logger.debug("Navegando a detalle: %s", url)
        self._delay()
        try:
            resp = self._page.goto(url, timeout=self.config.request_timeout * 1000, wait_until="domcontentloaded")
            if resp and resp.status >= 400:
                logger.error("HTTP %d al pedir %s", resp.status, url)
                return None
            self._page.evaluate(f"window.scrollBy(0, {random.randint(200, 600)})")
            self._page.wait_for_timeout(random.randint(800, 1800))
            soup = BeautifulSoup(self._page.content(), "html.parser")
            if self._is_blocked(soup):
                logger.warning("Pagina bloqueada detectada, rotando contexto | url=%s", url)
                self._restart_context()
                return None
            logger.debug("HTTP %d | url=%s", resp.status if resp else 0, url)
            return soup
        except Exception as exc:
            logger.error("Error navegando a %s: %s", url, exc)
            return None

    @staticmethod
    def _text(soup: BeautifulSoup, selector: str) -> str | None:
        node = soup.select_one(selector)
        if node is None:
            return None
        text = node.get_text(" ", strip=True)
        return text or None

    @staticmethod
    def _parse_price(price_text: str | None) -> float | None:
        if not price_text:
            return None
        normalized = re.sub(r"[^\d]", "", price_text)
        return float(normalized) if normalized else None

    @staticmethod
    def _parse_currency(currency_symbol: str | None) -> str | None:
        if not currency_symbol:
            return None
        symbol = currency_symbol.strip().upper()
        if "U$S" in symbol or "USD" in symbol or "US$" in symbol:
            return "USD"
        if "$" in symbol:
            return "ARS"
        return None

    @staticmethod
    def _extract_listing_id(url: str) -> str:
        match = MELI_LISTING_ID_PATTERN.search(url)
        return match.group(1) if match else url

    @staticmethod
    def _extract_location(soup: BeautifulSoup) -> str | None:
        for selector in (
            "div.ui-vip-location__subtitle",
            "p.ui-pdp-color--GRAY[class*='location']",
            "h2.ui-pdp-color--GRAY",
        ):
            value = MercadoLibreSpider._text(soup, selector)
            if value:
                return value
        return None

    @staticmethod
    def _extract_expenses(soup: BeautifulSoup) -> float | None:
        for row in soup.select("tr.andes-table__row"):
            cells = row.find_all("td")
            if len(cells) >= 2 and "expensa" in cells[0].get_text("", strip=True).lower():
                raw = re.sub(r"[^\d]", "", cells[1].get_text(" ", strip=True))
                if raw and raw != "0":
                    return float(raw)
        for selector in (
            "p.ui-pdp-maintenance-fee-ltr span",
            "span.ui-pdp-maintenance-fee-price__price",
        ):
            text = MercadoLibreSpider._text(soup, selector)
            if not text:
                continue
            cleaned = re.sub(r"[^\d]", "", text)
            if cleaned:
                return float(cleaned)
        return None

    @staticmethod
    def _extract_numeric_feature(soup: BeautifulSoup, pattern: str) -> float | None:
        for selector in (
            "div.ui-pdp-highlighted-specs-res__icon-label .ui-pdp-label span",
            ".ui-pdp-highlighted-specs-res .ui-pdp-label",
        ):
            for node in soup.select(selector):
                match = re.search(pattern, node.get_text(" ", strip=True), flags=re.IGNORECASE)
                if not match:
                    continue
                number = match.group(1).replace(".", "").replace(",", ".")
                try:
                    return float(number)
                except ValueError:
                    continue
        return None

    @staticmethod
    def _extract_coordinates(soup: BeautifulSoup) -> tuple[float | None, float | None]:
        img = soup.select_one("img[data-testid='static-map']")
        if not img:
            return None, None
        src = img.get("src", "")
        match = re.search(r"center=([-\d.]+)%2C([-\d.]+)", src)
        if match:
            return float(match.group(1)), float(match.group(2))
        return None, None

    @staticmethod
    def _extract_from_specs_table(soup: BeautifulSoup, key_contains: str) -> float | None:
        for row in soup.select("tr.andes-table__row"):
            th = row.find("th")
            td = row.find("td")
            if th and td and key_contains.lower() in th.get_text("", strip=True).lower():
                raw = re.sub(r"[^\d]", "", td.get_text("", strip=True))
                if raw:
                    return float(raw)
        return None

    @staticmethod
    def _extract_seller(soup: BeautifulSoup) -> str | None:
        for selector in (
            "div.ui-vip-profile-info__info-link h3",
            "div.ui-vip-seller-profile h3",
        ):
            node = soup.select_one(selector)
            if node:
                text = node.get_text(" ", strip=True)
                if text:
                    return text
        return None

    @staticmethod
    def _extract_specifications(soup: BeautifulSoup) -> list[str]:
        specs: list[str] = []
        seen: set[str] = set()
        for row in soup.select("tr.andes-table__row"):
            th = row.find("th")
            td = row.find("td")
            if not (th and td):
                continue
            key = th.get_text(" ", strip=True)
            value = td.get_text(" ", strip=True)
            entry = f"{key}: {value}"
            if key and value and entry not in seen:
                seen.add(entry)
                specs.append(entry)
        return specs

    @staticmethod
    def _extract_published_datetime(soup: BeautifulSoup) -> datetime | None:
        # El texto "Publicado hace N X" aparece en el HTML renderizado
        html_text = str(soup)
        m = re.search(r'Publicado\s+(hace\s+\d+\s+\w+|hoy|ayer)', html_text, re.I)
        if m:
            parsed = MercadoLibreSpider._parse_relative_time(m.group(1))
            if parsed:
                return parsed
        # Fallback: selectores legacy
        for selector in (
            ".ui-pdp-header__bottom-subtitle span",
            ".ui-pdp-header__subtitle span.ui-pdp-subtitle",
        ):
            text = MercadoLibreSpider._text(soup, selector)
            if text:
                parsed = MercadoLibreSpider._parse_relative_time(text)
                if parsed:
                    return parsed
        return None

    @staticmethod
    def _parse_relative_time(text: str) -> datetime | None:
        normalized = text.strip().lower()
        if "|" in normalized:
            normalized = normalized.split("|")[-1].strip()

        now = datetime.utcnow()
        if "hoy" in normalized:
            return now
        if "ayer" in normalized:
            return now.replace(microsecond=0) - timedelta(days=1)

        match = re.search(
            r"hace\s+(\d+)\s+(minutos?|horas?|d[ií]as?|semanas?|meses?|a[ñn]os?)",
            normalized,
        )
        if not match:
            return None

        amount = int(match.group(1))
        unit = match.group(2)
        now = now.replace(microsecond=0)
        if unit.startswith("minuto"):
            return now - timedelta(minutes=amount)
        if unit.startswith("hora"):
            return now - timedelta(hours=amount)
        if unit.startswith("d"):
            return now - timedelta(days=amount)
        if unit.startswith("semana"):
            return now - timedelta(weeks=amount)
        if unit.startswith("mes"):
            return now - timedelta(days=amount * 30)
        if unit.startswith("a"):
            return now - timedelta(days=amount * 365)
        return None


def scrape_mercadolibre(start_urls: Iterable[str], max_pages: int = 3) -> list[Listing]:
    with MercadoLibreSpider(config=SpiderConfig(max_pages=max_pages)) as spider:
        return spider.scrape(start_urls)
