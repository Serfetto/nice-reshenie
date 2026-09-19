"""Настройки: переменные окружения (.env) и YAML-конфиги из config/."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

# 0.1.0 — робастный краткосрочный прогноз протонов с диагностикой разброса,
# метеорные потоки в MMOD, исключение пристыкованных кораблей, причины уверенности.
ALGORITHM_VERSION = "0.1.0"

DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
RAW_DIR = DATA_DIR / "raw"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'app.db').as_posix()}")


def secret(name: str) -> str | None:
    value = os.getenv(name)
    return value or None


def _load_yaml(name: str) -> dict:
    path = ROOT / "config" / name
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@lru_cache
def sources() -> dict:
    return _load_yaml("sources.yaml")


@lru_cache
def thresholds() -> dict:
    return _load_yaml("thresholds.yaml")


def config_digest() -> dict:
    """Хэши файлов настроек: по ним сохранённый расчёт однозначно связывается с порогами и источниками."""
    import hashlib
    return {name: hashlib.sha256((ROOT / "config" / name).read_bytes()).hexdigest()[:16]
            for name in ("thresholds.yaml", "sources.yaml")}


def source_cfg(name: str) -> dict:
    cfg = sources().get(name)
    if cfg is None:
        raise KeyError(f"Источник {name} не описан в config/sources.yaml")
    return cfg
