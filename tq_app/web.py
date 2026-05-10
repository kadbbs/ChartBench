from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from tq_app.service import MarketDataService


def create_app(service: MarketDataService, project_root: Path) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(project_root / "templates"),
        static_folder=str(project_root / "static"),
    )

    css_asset = project_root / "static" / "styles.css"
    js_asset = project_root / "static" / "app.js"
    asset_versions = {
        "styles_css": str(int(css_asset.stat().st_mtime)) if css_asset.exists() else "0",
        "app_js": str(int(js_asset.stat().st_mtime)) if js_asset.exists() else "0",
    }

    @app.get("/")
    def index() -> str:
        return render_template("index.html", asset_versions=asset_versions)

    @app.get("/api/config")
    def api_config() -> Any:
        provider = request.args.get("provider", "").strip() or None
        return jsonify(service.get_config(provider=provider))

    @app.get("/api/snapshot")
    def api_snapshot() -> Any:
        parsed = _parse_snapshot_request()
        try:
            return jsonify(service.get_snapshot(**parsed))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.get("/api/stream")
    def api_stream() -> Response:
        parsed = _parse_snapshot_request()

        def encode_event(event: str, payload: dict[str, Any]) -> str:
            return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

        @stream_with_context
        def generate():
            last_version: int | None = None
            try:
                snapshot = service.get_snapshot(**parsed)
                stream_meta = snapshot.get("stream") or {}
                last_version = int(stream_meta.get("version") or 0)
                ready_deadline = time.monotonic() + 3.0
                while stream_meta.get("last_kline_at") is None and time.monotonic() < ready_deadline:
                    remaining = max(ready_deadline - time.monotonic(), 0.1)
                    next_version = service.wait_for_update(
                        symbol=parsed.get("symbol"),
                        provider=parsed.get("provider"),
                        duration_seconds=parsed.get("duration_seconds"),
                        bar_mode=parsed.get("bar_mode"),
                        range_ticks=parsed.get("range_ticks"),
                        brick_length=parsed.get("brick_length"),
                        data_length=parsed.get("data_length"),
                        last_version=last_version,
                        timeout=min(0.5, remaining),
                    )
                    if next_version != last_version:
                        snapshot = service.get_snapshot(**parsed)
                        stream_meta = snapshot.get("stream") or {}
                        last_version = int(stream_meta.get("version") or next_version)
                yield encode_event("snapshot", snapshot)
            except Exception as exc:
                yield encode_event("stream-error", {"error": str(exc)})

            while True:
                try:
                    next_version = service.wait_for_update(
                        symbol=parsed.get("symbol"),
                        provider=parsed.get("provider"),
                        duration_seconds=parsed.get("duration_seconds"),
                        bar_mode=parsed.get("bar_mode"),
                        range_ticks=parsed.get("range_ticks"),
                        brick_length=parsed.get("brick_length"),
                        data_length=parsed.get("data_length"),
                        last_version=last_version,
                        timeout=15.0,
                    )
                    if next_version == last_version:
                        yield encode_event("heartbeat", {"version": last_version})
                        continue
                    snapshot = service.get_snapshot(**parsed)
                    stream_meta = snapshot.get("stream") or {}
                    last_version = int(stream_meta.get("version") or next_version)
                    yield encode_event("snapshot", snapshot)
                except GeneratorExit:
                    break
                except Exception as exc:
                    yield encode_event("stream-error", {"error": str(exc)})

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    def _parse_snapshot_request() -> dict[str, Any]:
        indicator_param = request.args.get("indicators", "")
        indicator_params_raw = request.args.get("indicator_params", "")
        symbol = request.args.get("symbol", "").strip() or None
        provider = request.args.get("provider", "").strip() or None
        duration_raw = request.args.get("duration_seconds", "").strip()
        bar_mode = request.args.get("bar_mode", "").strip() or None
        range_ticks_raw = request.args.get("range_ticks", "").strip()
        brick_length_raw = request.args.get("brick_length", "").strip()
        data_length_raw = request.args.get("data_length", "").strip()
        indicator_ids = [item.strip() for item in indicator_param.split(",") if item.strip()]
        indicator_params: dict[str, dict[str, Any]] | None = None
        duration_seconds: int | None = None
        range_ticks: int | None = None
        brick_length: int | None = None
        data_length: int | None = None
        if indicator_params_raw:
            indicator_params = json.loads(indicator_params_raw)
        if duration_raw:
            duration_seconds = int(duration_raw)
        if range_ticks_raw:
            range_ticks = int(range_ticks_raw)
        if brick_length_raw:
            brick_length = int(brick_length_raw)
        if data_length_raw:
            data_length = int(data_length_raw)
        return {
            "indicator_ids": indicator_ids or None,
            "indicator_params": indicator_params,
            "symbol": symbol,
            "provider": provider,
            "duration_seconds": duration_seconds,
            "bar_mode": bar_mode,
            "range_ticks": range_ticks,
            "brick_length": brick_length,
            "data_length": data_length,
        }

    return app
