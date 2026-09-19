"""Задания расчёта: создание, выполнение в пуле потоков, прогресс, хранение результата."""
from __future__ import annotations

import logging
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select, update

from common import config
from common.db import get_engine, runs
from common.timeutil import utcnow
from services.assessment.runner import RunError, execute, jsonable
from services.assessment.schemas import RunRequest

log = logging.getLogger(__name__)
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="run")


def new_id() -> str:
    return "r_" + uuid.uuid4().hex[:12]


def _set(run_id: str, **values) -> None:
    with get_engine().begin() as conn:
        conn.execute(update(runs).where(runs.c.id == run_id).values(**values))


def create(kind: str, request: dict, parent_id: str | None = None) -> str:
    run_id = new_id()
    with get_engine().begin() as conn:
        conn.execute(runs.insert().values(id=run_id, kind=kind, parent_id=parent_id, created_at=utcnow(),
                                          status="queued", progress={"stage": "queued", "pct": 0},
                                          request=request, algorithm_version=config.ALGORITHM_VERSION))
    return run_id


def fail_interrupted() -> int:
    """Задания живут в памяти процесса и не переживают перезапуск: незавершённые помечаются прерванными,
    чтобы интерфейс не ждал их бесконечно."""
    with get_engine().begin() as conn:
        res = conn.execute(update(runs).where(runs.c.status.in_(("queued", "running"))).values(
            status="failed", finished_at=utcnow(),
            error="Расчёт прерван перезапуском сервиса оценки — запустите его заново."))
    if res.rowcount:
        log.warning("Помечено прерванными незавершённых расчётов: %d", res.rowcount)
    return res.rowcount


def _progress_writer(run_id: str):
    last = [0.0]

    def write(stage: str, pct: int):
        now = time.monotonic()
        if pct in (0, 100) or now - last[0] > 0.3:
            last[0] = now
            _set(run_id, status="running", progress={"stage": stage, "pct": pct})
    return write


# известны заранее (геометрия орбиты, годовой прогноз) — сами по себе не делают вариант неблагоприятным
BACKGROUND_REASONS = {"saa", "meteor_shower"}


def summarize(result: dict) -> dict:
    """Краткий итог расчёта для списка сохранённых: ответ, надёжность, лучший и плановый варианты."""
    wins = {w["id"]: w for w in result.get("windows") or []}
    rec = result.get("recommendation") or {}

    def brief(w: dict | None) -> dict | None:
        if w is None:
            return None
        return {"id": w["id"], "start": w["start"], "end": w["end"], "status": w["status"],
                "worst": {m: st["worst"] for m, st in w["mechanisms"].items()}}

    best = wins.get(rec.get("window"))
    adverse = bool(best) and any(st["worst"] == "undesirable" and set(st.get("reasons") or {}) - BACKGROUND_REASONS
                                 for st in best["mechanisms"].values())
    counts: dict[str, int] = {}
    for w in wins.values():
        counts[w["status"]] = counts.get(w["status"], 0) + 1
    return {"mode": result.get("mode"), "as_of": result.get("as_of"),
            "duration_min": (result.get("search") or {}).get("duration_min"),
            "rec_status": rec.get("status"), "confidence": rec.get("confidence"), "adverse": adverse,
            "best": brief(best), "planned": brief(wins.get(result.get("planned"))),
            "n_windows": len(wins), "counts": counts,
            "source_issues": [s["source"] for s in result.get("sources") or [] if s.get("state") not in ("ok", "frozen")],
            "reconstruction": bool(result.get("reconstruction"))}


def _run_assessment(run_id: str, req: RunRequest) -> None:
    try:
        _set(run_id, status="running", progress={"stage": "start", "pct": 1})
        result = execute(req, progress=_progress_writer(run_id))
        result["run_id"] = run_id
        _set(run_id, status="done", result=result, summary=summarize(result), finished_at=utcnow(),
             progress={"stage": "done", "pct": 100})
    except RunError as e:
        _set(run_id, status="failed", error=str(e), finished_at=utcnow())
    except Exception as e:
        log.error("Расчёт %s упал:\n%s", run_id, traceback.format_exc())
        _set(run_id, status="failed", error=f"Внутренняя ошибка: {type(e).__name__}: {e}", finished_at=utcnow())


def _run_verify(run_id: str, parent: dict, parent_request: dict) -> None:
    from services.assessment.verify import verify
    try:
        _set(run_id, status="running", progress={"stage": "verify", "pct": 10})
        result = jsonable(verify(parent, parent_request))
        result["run_id"] = run_id
        _set(run_id, status="done", result=result, finished_at=utcnow(), progress={"stage": "done", "pct": 100})
    except RunError as e:
        _set(run_id, status="failed", error=str(e), finished_at=utcnow())
    except Exception as e:
        log.error("Сверка %s упала:\n%s", run_id, traceback.format_exc())
        _set(run_id, status="failed", error=f"Внутренняя ошибка: {type(e).__name__}: {e}", finished_at=utcnow())


def submit_assessment(req: RunRequest) -> str:
    run_id = create("assessment", req.model_dump(mode="json"))
    _pool.submit(_run_assessment, run_id, req)
    return run_id


def submit_verify(parent_id: str) -> str:
    with get_engine().connect() as conn:
        row = conn.execute(select(runs).where(runs.c.id == parent_id)).first()
    if row is None:
        raise KeyError(parent_id)
    if row.status != "done" or row.kind != "assessment":
        raise RunError("Сверять можно только завершённый расчёт.")
    run_id = create("verify", {"parent_id": parent_id}, parent_id=parent_id)
    _pool.submit(_run_verify, run_id, row.result, row.request)
    return run_id
