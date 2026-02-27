from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request
from starlette.responses import Response

from .metrics import metrics


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def install_request_middleware(app: FastAPI) -> None:
    logger = logging.getLogger("beecount.access")
    configure_logging()

    @app.middleware("http")
    async def request_observer(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid4().hex
        request.state.request_id = request_id
        start = time.perf_counter()
        metrics.inc("beecount_http_requests_total")

        status_code = 500
        response: Response | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            metrics.inc("beecount_http_errors_total")
            raise
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            metrics.inc(f"beecount_http_status_{status_code // 100}xx_total")
            log_payload = {
                "event": "http_request",
                "requestId": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": status_code,
                "durationMs": round(elapsed_ms, 2),
            }
            logger.info(json.dumps(log_payload, ensure_ascii=False))

        if response is None:
            raise RuntimeError("response missing")
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.2f}"
        return response
