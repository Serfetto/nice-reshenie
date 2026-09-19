"""Командная строка сервиса сбора.

python -m services.ingest.cli init-db
python -m services.ingest.cli live [--source NAME ...]
python -m services.ingest.cli backfill --from 2024-04-27 --to 2024-07-02 [--source NAME ...]
python -m services.ingest.cli bootstrap          # только недостающие дни архива (период — config/sources.yaml)
python -m services.ingest.cli reparse --source NAME
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date

from sqlalchemy import select

from common import config
from common.db import get_engine, init_db, raw_files
from services.ingest import registry
from services.ingest.store import mark_parsed



def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="ingest")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    lp = sub.add_parser("live")
    lp.add_argument("--source", action="append")
    bp = sub.add_parser("backfill")
    bp.add_argument("--from", dest="start", required=True, type=date.fromisoformat)
    bp.add_argument("--to", dest="end", required=True, type=date.fromisoformat)
    bp.add_argument("--source", action="append")
    sub.add_parser("bootstrap")
    rp = sub.add_parser("reparse")
    rp.add_argument("--source", required=True)
    args = p.parse_args(argv)

    engine = get_engine()
    init_db(engine)
    if args.cmd == "init-db":
        print("ok")
    elif args.cmd == "live":
        for name in args.source or [n for n, a in registry.ADAPTERS.items() if a.has_live]:
            print(json.dumps(registry.run_live(engine, name, force=True).as_dict(), ensure_ascii=False))
    elif args.cmd == "backfill":
        for name in args.source or registry.BACKFILL_ORDER:
            res = registry.run_backfill(engine, name, args.start, args.end)
            print(json.dumps(res.as_dict(), ensure_ascii=False))
    elif args.cmd == "bootstrap":
        from services.ingest.bootstrap import ensure_archive
        st = ensure_archive(engine)
        print(json.dumps({k: v for k, v in st.items() if k != "sources"}, ensure_ascii=False))
        for name, s in st["sources"].items():
            print(json.dumps({"source": name, **s}, ensure_ascii=False))
    elif args.cmd == "reparse":
        adapter = registry.get(args.source)
        with engine.connect() as conn:
            rows = conn.execute(select(raw_files).where(raw_files.c.source == args.source)
                                .order_by(raw_files.c.id)).fetchall()
        total = 0
        for r in rows:
            content = (config.RAW_DIR / r.path).read_bytes()
            with engine.begin() as conn:
                try:
                    n = adapter.parse(conn, r.id, content, r.url, r.fetched_at)
                    mark_parsed(conn, r.id, "ok", n)
                    total += n
                except Exception as e:
                    mark_parsed(conn, r.id, "error", 0, str(e))
        print(json.dumps({"source": args.source, "files": len(rows), "new_items": total}, ensure_ascii=False))


if __name__ == "__main__":
    main()
