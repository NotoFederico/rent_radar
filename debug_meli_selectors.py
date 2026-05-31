"""
Diagnóstico de selectores MercadoLibre.
Navega a la URL de búsqueda, prueba los selectores candidatos,
y guarda el HTML para inspección manual si ninguno matchea.
"""
from __future__ import annotations

import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = (
    "https://inmuebles.mercadolibre.com.ar/alquiler/mas-de-3-ambientes/"
    "bsas-gba-oeste/la-matanza/ramos-mejia-o-villa-luzuriaga-o-san-justo/"
    "_COVERED*AREA_70-*_NoIndex_True_Cocheras_1"
)

CANDIDATES = [
    "li.ui-search-layout__item a.poly-component__title",
    "li.ui-search-layout__item a[href*='MLAe']",
    "li.ui-search-layout__item a[href*='/p/']",
    "a.poly-component__title",
    ".poly-card a[href*='inmuebles']",
    "li.ui-search-layout__item a",
]

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(user_agent=UA, locale="es-AR")
    ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    page = ctx.new_page()

    print(f"Navegando a {URL} …")
    page.goto(URL, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(3_000)

    html = page.content()

    print("\n--- Probando selectores ---")
    found_any = False
    for sel in CANDIDATES:
        nodes = page.query_selector_all(sel)
        hrefs = [n.get_attribute("href") for n in nodes[:3] if n.get_attribute("href")]
        status = f"{len(nodes)} resultados" if nodes else "0 resultados"
        mark = "✓" if nodes else "✗"
        print(f"  {mark}  {sel!r:60s}  {status}")
        if hrefs:
            for h in hrefs:
                print(f"       → {h[:90]}")
        if nodes:
            found_any = True

    if not found_any:
        out = Path("debug_meli.html")
        out.write_text(html, encoding="utf-8")
        print(f"\nNingún selector matcheó. HTML guardado en {out} para inspección manual.")
        print("Abrilo en el browser (Ctrl+F para buscar clases de los links de publicaciones).")
    else:
        print("\nActualizá el selector en spiders/mercadolibre.py línea 90.")

    browser.close()
