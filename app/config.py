from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

NEON_DATABASE_URL = os.environ["NEON_DATABASE_URL"]
