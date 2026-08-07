from hashlib import sha256
from pathlib import Path

import yaml

from app.infrastructure.catalogs.models import AnalyticsCatalog


def load_catalog(path: Path) -> tuple[AnalyticsCatalog, str]:
    raw = path.read_bytes()
    catalog = AnalyticsCatalog.model_validate(yaml.safe_load(raw))
    return catalog, sha256(raw).hexdigest()
