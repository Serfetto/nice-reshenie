"""Загрузка по HTTP(S) и FTP с повторными попытками."""
from __future__ import annotations

import time
import urllib.request
from dataclasses import dataclass

import httpx

USER_AGENT = "kosmohack-eva-risk/0.1 (research prototype)"


class FetchError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class Response:
    url: str
    status: int
    content: bytes
    content_type: str | None


def get(url: str, *, timeout: float = 60, retries: int = 2, client: httpx.Client | None = None,
        not_found_ok: bool = False) -> Response | None:
    """GET с повторами. 404 при not_found_ok возвращает None (нужно для архивов с пропусками)."""
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            if url.startswith("ftp://"):
                with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 — фиксированные адреса из конфига
                    return Response(url, 200, r.read(), None)
            c = client or httpx.Client(timeout=timeout, follow_redirects=True,
                                       headers={"User-Agent": USER_AGENT})
            try:
                r = c.get(url)
            finally:
                if client is None:
                    c.close()
            if r.status_code == 404 and not_found_ok:
                return None
            if r.status_code >= 400:
                raise FetchError(f"HTTP {r.status_code} для {url}", r.status_code)
            return Response(url, r.status_code, r.content, r.headers.get("content-type"))
        except FetchError as e:
            last_err = e
            if e.status and 400 <= e.status < 500 and e.status != 429:
                break
        except Exception as e:  # сеть, таймаут, FTP
            last_err = e
        if attempt < retries:
            time.sleep(2 * (attempt + 1))
    raise FetchError(f"Не удалось получить {url}: {last_err}")
