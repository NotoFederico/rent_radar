"""Corre dbt run con el tipo de cambio USD oficial del momento."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import requests


def fetch_tipo_cambio() -> int:
    r = requests.get("https://dolarapi.com/v1/dolares/oficial", timeout=10)
    r.raise_for_status()
    data = r.json()
    venta = data.get("venta") or data.get("compra")
    return int(round(venta))


def main() -> None:
    try:
        tc = fetch_tipo_cambio()
        print(f"Tipo de cambio USD oficial: ${tc}")
    except Exception as e:
        tc = 1450
        print(f"No se pudo obtener el tipo de cambio ({e}), usando fallback: ${tc}")

    vars_json = json.dumps({"tipo_cambio_usd": tc})
    dbt_bin = os.path.join(os.path.dirname(sys.executable), "dbt")
    cmd = [dbt_bin, "run", "--vars", vars_json]

    extra = sys.argv[1:]
    if extra:
        cmd += extra

    print(f"Ejecutando: {' '.join(cmd)}\n")
    result = subprocess.run(cmd, cwd="analytics")
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
