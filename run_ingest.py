"""
Corre los tres spiders en paralelo, simulando el step de ingest de Prefect.
Editá SOURCES abajo para cambiar las URLs o el max de páginas.
"""
from __future__ import annotations

import subprocess
import sys
import threading
from dataclasses import dataclass, field


@dataclass
class Source:
    name: str
    urls: list[str]
    max_pages: int = 3


SOURCES: list[Source] = [
    Source(
        name="zonaprop",
        urls=[
            "https://www.zonaprop.com.ar/departamentos-alquiler-palermo.html",
        ],
    ),
    Source(
        name="argenprop",
        urls=[
            "https://www.argenprop.com/casas-o-departamentos-o-ph/alquiler/haedo-o-ramos-mejia-o-san-justo-la-matanza-o-villa-luzuriaga/3-ambientes-o-4-ambientes-o-5-o-mas-ambientes?1-o-mas-cocheras",
        ],
    ),
    Source(
        name="mercadolibre",
        urls=[
            "https://inmuebles.mercadolibre.com.ar/alquiler/mas-de-3-ambientes/bsas-gba-oeste/la-matanza/ramos-mejia-o-villa-luzuriaga-o-san-justo/_COVERED*AREA_70-*_NoIndex_True_Cocheras_1",
        ],
    ),
]


def _run_source(source: Source) -> int:
    cmd = [sys.executable, "main.py", "--ingest", "--source", source.name]
    for url in source.urls:
        cmd += ["--start-url", url]
    cmd += ["--max-pages", str(source.max_pages)]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    for line in proc.stdout:
        print(f"[{source.name}] {line}", end="")
    proc.wait()
    return proc.returncode


def main() -> None:
    threads: list[threading.Thread] = []
    results: dict[str, int] = {}

    for source in SOURCES:
        def _target(s=source):
            results[s.name] = _run_source(s)

        t = threading.Thread(target=_target, name=source.name)
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print("\n--- Resultado ---")
    ok = True
    for name, code in results.items():
        status = "OK" if code == 0 else f"ERROR (exit {code})"
        print(f"  {name}: {status}")
        if code != 0:
            ok = False

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
