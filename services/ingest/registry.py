"""Реестр адаптеров и запуск загрузок со статусами."""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.engine import Engine

from common.db import notify
from services.ingest.adapters.base import Adapter, IngestResult
from services.ingest.adapters.goes_protons import GoesProtons
from services.ingest.adapters.kp import KpForecast, KpObserved
from services.ingest.adapters.meteors import MeteorForecast
from services.ingest.adapters.orbital import CatalogSpaceTrack, IssCelestrak, IssSpaceTrack
from services.ingest.adapters.swpc_alerts import SwpcAlerts
from services.ingest.adapters.swpc_text import Swpc3Day, SwpcRsga
from services.ingest.store import is_paused, status_error, status_success

log = logging.getLogger(__name__)

ADAPTERS: dict[str, Adapter] = {a.name: a for a in [
    SwpcAlerts(), GoesProtons(), KpObserved(), KpForecast(), Swpc3Day(), SwpcRsga(),
    IssSpaceTrack(), IssCelestrak(), CatalogSpaceTrack(), MeteorForecast(),
]}

# порядок загрузки архива: сначала лёгкие и важные для отсечки источники, каталог — последним (самый тяжёлый)
BACKFILL_ORDER = ["swpc_alerts", "iss_spacetrack", "kp_observed", "swpc_3day", "swpc_rsga", "meteor_forecast",
                  "goes_protons", "catalog_spacetrack"]


def get(name: str) -> Adapter:
    if name not in ADAPTERS:
        raise KeyError(f"Неизвестный источник: {name}")
    return ADAPTERS[name]


def run_live(engine: Engine, name: str, *, force: bool = False) -> IngestResult:
    adapter = get(name)
    with engine.begin() as conn:
        if not force and is_paused(conn, name):
            return IngestResult(name, skipped=1)
    try:
        result = adapter.live(engine)
    except Exception as e:
        log.warning("%s: ошибка загрузки: %s", name, e)
        with engine.begin() as conn:
            status_error(conn, name, str(e))
        return IngestResult(name, errors=[str(e)])
    with engine.begin() as conn:
        if result.errors and not result.files:
            status_error(conn, name, "; ".join(result.errors))
        else:
            status_success(conn, name, result.new_items)
            if result.new_items:
                notify(conn, "new_data", name)
    return result


def run_backfill(engine: Engine, name: str, start: date, end: date) -> IngestResult:
    adapter = get(name)
    if not adapter.has_backfill:
        return IngestResult(name, skipped=1)
    result = adapter.backfill(engine, start, end)
    if result.new_items:
        with engine.begin() as conn:
            notify(conn, "new_data", name)
    return result
