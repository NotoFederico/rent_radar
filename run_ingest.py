"""
Corre los tres spiders en paralelo, simulando el step de ingest de Prefect.
Editá SOURCES abajo para cambiar las URLs o el max de páginas.
"""
from __future__ import annotations

import select
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field


@dataclass
class Source:
    name: str
    urls: list[str]
    max_pages: int = 3


SOURCES: list[Source] = [
    Source(
        name="zonaprop",
        max_pages=20,
        urls=[
            "https://www.zonaprop.com.ar/casas-departamentos-ph-alquiler-mataderos-villa-luzuriaga-ramos-mejia-haedo-san-justo-la-matanza-ciudadela-tres-de-febrero-villa-sarmiento-moron-desde-3-hasta-5-ambientes-mas-de-1-garage-mas-70-m2-cubiertos-orden-publicado-descendente.html",
            #"https://www.zonaprop.com.ar/casas-departamentos-ph-alquiler-mataderos-villa-luro-caballito-naon-mataderos-desde-3-hasta-5-ambientes-mas-de-1-garage-mas-70-m2-cubiertos.html",
        ],
    ),
    Source(
        name="argenprop",
        max_pages=20,
        urls=[
            "https://www.argenprop.com/casas-o-departamentos-o-ph/alquiler/haedo-o-ramos-mejia-o-san-justo-la-matanza-o-villa-luzuriaga-o-villa-sarmiento/3-ambientes-o-4-ambientes-o-5-o-mas-ambientes?1-o-mas-cocheras",
            #"https://www.argenprop.com/casas-o-departamentos-o-ph/alquiler/caballito-o-mataderos-o-villa-luro/3-ambientes-o-4-ambientes-o-5-o-mas-ambientes?1-o-mas-cocheras",
        ],
    ),
    Source(
        name="mercadolibre",
        max_pages=4,
        urls=[
            "https://inmuebles.mercadolibre.com.ar/alquiler/mas-de-3-ambientes/bsas-gba-oeste/la-matanza/ramos-mejia-o-villa-luzuriaga-o-san-justo/_COVERED*AREA_70-*_NoIndex_True_Cocheras_1",
            "https://inmuebles.mercadolibre.com.ar/alquiler/mas-de-3-ambientes/bsas-gba-oeste/moron/villa-sarmiento-o-haedo/_COVERED*AREA_70-*_NoIndex_True_Cocheras_1",
            #"https://inmuebles.mercadolibre.com.ar/alquiler/mas-de-3-ambientes/capital-federal/caballito-o-villa-luro-o-mataderos/_COVERED*AREA_70-*_NoIndex_True_Cocheras_1",
            
        ],
    ),
]


INACTIVITY_TIMEOUT = 120  # segundos sin output → proceso colgado


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

    while True:
        ready, _, _ = select.select([proc.stdout], [], [], INACTIVITY_TIMEOUT)
        if ready:
            line = proc.stdout.readline()
            if not line:  # EOF — proceso terminó
                break
            print(f"[{source.name}] {line}", end="", flush=True)
        else:
            print(
                f"\n[{source.name}] Sin actividad por {INACTIVITY_TIMEOUT}s — proceso colgado, matando",
                flush=True,
            )
            proc.kill()
            proc.wait()
            return 1

    proc.wait()
    return proc.returncode


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=None, help="Correr solo esta fuente (zonaprop|argenprop|mercadolibre)")
    args = parser.parse_args()

    sources = [s for s in SOURCES if s.name == args.source] if args.source else SOURCES

    if not sources:
        print(f"Fuente desconocida: {args.source}. Opciones: {[s.name for s in SOURCES]}")
        sys.exit(1)

    if len(sources) == 1:
        code = _run_source(sources[0])
        sys.exit(code)

    threads: list[threading.Thread] = []
    results: dict[str, int] = {}

    for source in sources:
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
