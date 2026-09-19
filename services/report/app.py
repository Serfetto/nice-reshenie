"""Сервис report: веб-консоль аналитика, краткая сводка, полный отчёт, экспорт, доставка оповещений (SSE).

Браузер обращается только к этому сервису; запросы к assessment и ingest проксируются
(/api/a/... и /api/i/...), поэтому наружу достаточно открыть один порт.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import zipfile
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from common.timeutil import msk
from services.report import render

HERE = Path(__file__).resolve().parent
ASSESSMENT_URL = os.getenv("ASSESSMENT_URL", "http://localhost:8002").rstrip("/")
INGEST_URL = os.getenv("INGEST_URL", "http://localhost:8001").rstrip("/")

app = FastAPI(title="report — интерфейс и отчёты ВКД")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["msk"] = msk
templates.env.globals.update(STATUS_TEXT=render.STATUS_TEXT, REC_TEXT=render.REC_TEXT,
                             MODE_TEXT=render.MODE_TEXT, KIND_TEXT=render.KIND_TEXT, MECH_TEXT=render.MECH_TEXT)
_client = httpx.AsyncClient(timeout=120)


@app.get("/health")
async def health():
    out = {"report": "ok"}
    for name, url in (("assessment", ASSESSMENT_URL), ("ingest", INGEST_URL)):
        try:
            r = await _client.get(url + "/health", timeout=5)
            out[name] = "ok" if r.status_code == 200 else f"HTTP {r.status_code}"
        except Exception as e:
            out[name] = f"недоступен: {type(e).__name__}"
    return out


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


# ---------- прокси к сервисам ----------

async def _proxy(base: str, path: str, request: Request) -> Response:
    url = f"{base}/{path}"
    body = await request.body()
    try:
        r = await _client.request(request.method, url, params=request.query_params, content=body or None,
                                  headers={"content-type": request.headers.get("content-type", "application/json")})
    except httpx.HTTPError as e:
        return Response(json.dumps({"detail": f"Сервис недоступен: {type(e).__name__}"}, ensure_ascii=False),
                        status_code=503, media_type="application/json")
    return Response(r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type", "application/json"))


@app.api_route("/api/a/{path:path}", methods=["GET", "POST", "DELETE"])
async def proxy_assessment(path: str, request: Request):
    return await _proxy(ASSESSMENT_URL, path, request)


@app.api_route("/api/i/{path:path}", methods=["GET", "POST"])
async def proxy_ingest(path: str, request: Request):
    return await _proxy(INGEST_URL, path, request)


# ---------- оповещения: поток SSE ----------

@app.get("/api/alerts/stream")
async def alerts_stream(request: Request, since_id: int = 0):
    async def gen():
        last = since_id
        if last == 0:  # новые подключения получают только новые оповещения
            try:
                r = await _client.get(ASSESSMENT_URL + "/alerts", params={"since_id": 0, "limit": 500})
                items = r.json()
                last = items[-1]["id"] if items else 0
            except Exception:
                pass
        idle = 0
        while True:
            if await request.is_disconnected():
                break
            try:
                r = await _client.get(ASSESSMENT_URL + "/alerts", params={"since_id": last}, timeout=10)
                for a in r.json():
                    last = a["id"]
                    yield f"id: {a['id']}\nevent: alert\ndata: {json.dumps(a, ensure_ascii=False)}\n\n"
                    idle = 0
            except Exception:
                yield "event: error\ndata: {\"detail\": \"assessment недоступен\"}\n\n"
            idle += 1
            if idle % 8 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- отчёты и экспорт ----------

async def _load_run(run_id: str) -> dict:
    r = await _client.get(f"{ASSESSMENT_URL}/runs/{run_id}")
    if r.status_code == 404:
        raise HTTPException(404, "Нет такого расчёта")
    run = r.json()
    if run.get("status") != "done" or not run.get("result"):
        raise HTTPException(409, f"Расчёт не завершён: {run.get('status')} {run.get('error') or ''}")
    return run


def _report_context(run: dict) -> dict:
    res = run["result"]
    wins = {w["id"]: w for w in res["windows"]}
    rec = res["recommendation"]
    best = wins.get(rec.get("window"))
    planned = wins.get(res.get("planned"))
    ranked = sorted(res["windows"], key=lambda w: (["preferred", "equivalent", "worse", "requires_review",
                                                    "not_recommended"].index(w["status"]), w["start"]))
    return {"run": run, "res": res, "rec": rec, "best": best, "planned": planned, "ranked": ranked,
            "svg": render.class_strip_svg(res), "status_line": render.status_line(res)}


@app.get("/runs/{run_id}/report", response_class=HTMLResponse)
async def full_report(request: Request, run_id: str):
    run = await _load_run(run_id)
    return templates.TemplateResponse(request, "report.html", _report_context(run))


@app.get("/runs/{run_id}/brief", response_class=HTMLResponse)
async def brief(request: Request, run_id: str):
    run = await _load_run(run_id)
    return templates.TemplateResponse(request, "brief.html", _report_context(run))


@app.get("/runs/{run_id}/status-line", response_class=PlainTextResponse)
async def status_line(run_id: str):
    run = await _load_run(run_id)
    return render.status_line(run["result"])


def build_zip(run: dict, verification: dict | None = None) -> bytes:
    """Выгрузка расчёта: читаемые отчёт и сводка + машиночитаемые JSON/CSV (те же данные, что в консоли)."""
    ctx = _report_context(run)
    res = run["result"]
    report_html = templates.get_template("report.html").render({"request": None, **ctx})
    brief_html = templates.get_template("brief.html").render({"request": None, **ctx})
    manifest = {"run_id": run["run_id"], "algorithm_version": run.get("algorithm_version"), "config": res.get("config"),
                "mode": res["mode"],
                "as_of": res["as_of"], "data_cutoff": res["data_cutoff"], "request": run.get("request"),
                "sources": res["sources"], "raw_files": res["manifest"],
                "verification": verification.get("run_id") if verification else None,
                "note": "raw_id — идентификаторы сырых файлов в хранилище ingest (GET /raw/{raw_id})"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.html", report_html)
        z.writestr("brief.html", brief_html)
        z.writestr("run.json", json.dumps(run, ensure_ascii=False, indent=1))
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        z.writestr("series.csv", render.series_csv(res))
        z.writestr("windows.csv", render.windows_csv(res))
        z.writestr("intervals.csv", render.intervals_csv(res))
        if verification:
            z.writestr("verification.json", json.dumps(verification, ensure_ascii=False, indent=1))
    return buf.getvalue()


@app.get("/runs/{run_id}/export.zip")
async def export_zip(run_id: str):
    run = await _load_run(run_id)
    verification = None
    try:  # последняя завершённая сверка с фактом, если была
        r = await _client.get(f"{ASSESSMENT_URL}/runs", params={"parent_id": run_id, "limit": 5})
        done = [x for x in r.json() if x.get("kind") == "verify" and x.get("status") == "done"]
        if done:
            verification = (await _client.get(f"{ASSESSMENT_URL}/runs/{done[0]['run_id']}")).json()
    except Exception:
        pass
    return Response(build_zip(run, verification), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="vkd_{run_id}.zip"'})
