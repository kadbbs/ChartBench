from __future__ import annotations

import argparse
import logging
import signal
import socket
import threading
import sys
import webbrowser
from pathlib import Path

from dotenv import load_dotenv
from werkzeug.serving import BaseWSGIServer, ThreadedWSGIServer, make_server

from tq_app.service import MarketDataService
from tq_app.web import create_app

DEFAULT_PROVIDER = "binance"
DEFAULT_SYMBOL = "BTCUSDT"
DEFAULT_DURATION_SECONDS = 60
DEFAULT_DATA_LENGTH = 800
DEFAULT_REFRESH_MS = 200
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8050
DEFAULT_BAR_MODE = "time"
DEFAULT_RANGE_TICKS = 10
DEFAULT_BRICK_LENGTH = 10000


def runtime_project_root() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
    return Path(__file__).resolve().parent


class ServerThread(threading.Thread):
    def __init__(self, app, host: str, port: int) -> None:
        super().__init__(daemon=True)
        self.server = make_server(host, port, app)
        self.context = app.app_context()
        self.context.push()

    def run(self) -> None:
        self.server.serve_forever()

    def shutdown(self) -> None:
        self.server.shutdown()


class IPv6OnlyWSGIServer(BaseWSGIServer):
    def server_bind(self) -> None:
        if self.address_family == socket.AF_INET6:
            try:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            except (AttributeError, OSError):
                pass
        super().server_bind()


class IPv6OnlyThreadedWSGIServer(ThreadedWSGIServer):
    def server_bind(self) -> None:
        if self.address_family == socket.AF_INET6:
            try:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            except (AttributeError, OSError):
                pass
        super().server_bind()


class MultiServerThread(threading.Thread):
    def __init__(self, app, host: str, port: int) -> None:
        super().__init__(daemon=True)
        self.context = app.app_context()
        self.context.push()
        self.servers: list[BaseWSGIServer] = self._build_servers(app, host, port)

    def _build_servers(self, app, host: str, port: int) -> list[BaseWSGIServer]:
        normalized_host = host.strip()
        if normalized_host not in {"0.0.0.0", "::", ""}:
            return [make_server(normalized_host, port, app, threaded=True)]

        servers: list[BaseWSGIServer] = []
        ipv4_server = make_server("0.0.0.0", port, app, threaded=True)
        servers.append(ipv4_server)

        try:
            ipv6_server = IPv6OnlyThreadedWSGIServer("::", port, app)
        except OSError:
            ipv6_server = None
        if ipv6_server is not None:
            servers.append(ipv6_server)

        return servers

    def run(self) -> None:
        workers: list[threading.Thread] = []
        for server in self.servers:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            workers.append(worker)
        for worker in workers:
            worker.join()

    def shutdown(self) -> None:
        for server in self.servers:
            server.shutdown()


def display_url(host: str, port: int) -> str:
    normalized_host = host.strip()
    if normalized_host in {"0.0.0.0", "", "::"}:
        return f"http://0.0.0.0:{port}"
    if ":" in normalized_host:
        return f"http://[{normalized_host}]:{port}"
    return f"http://{normalized_host}:{port}"


def listening_summary(host: str, port: int) -> list[str]:
    normalized_host = host.strip()
    if normalized_host in {"0.0.0.0", ""}:
        return [f"http://0.0.0.0:{port}", f"http://[::]:{port}"]
    if normalized_host == "::":
        return [f"http://[::]:{port}"]
    return [display_url(normalized_host, port)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Binance 行情浏览器图表工作台")
    parser.add_argument("--provider", default=DEFAULT_PROVIDER, choices=[DEFAULT_PROVIDER], help="数据源名称，当前仅支持 binance")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="合约代码，例如 BTCUSDT")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION_SECONDS, help="K 线周期，单位秒")
    parser.add_argument("--length", type=int, default=DEFAULT_DATA_LENGTH, help="拉取 K 线数量")
    parser.add_argument("--brick-length", type=int, default=DEFAULT_BRICK_LENGTH, help="Range Bar / Renko 保留砖块数量")
    parser.add_argument("--refresh-ms", type=int, default=DEFAULT_REFRESH_MS, help="刷新间隔，单位毫秒")
    parser.add_argument(
        "--bar-mode",
        default=DEFAULT_BAR_MODE,
        choices=["time", "tick", "range", "renko"],
        help="图表类型: time / tick / range / renko",
    )
    parser.add_argument(
        "--range-ticks",
        type=int,
        default=DEFAULT_RANGE_TICKS,
        help="Range Bar / Renko 的价格跨度，单位 tick",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="监听地址")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口")
    parser.add_argument("--open-browser", action="store_true", help="启动后自动打开浏览器")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    project_root = runtime_project_root()
    load_dotenv(project_root / ".env")
    service = MarketDataService(
        provider=args.provider,
        symbol=args.symbol,
        duration_seconds=args.duration,
        data_length=args.length,
        brick_length=args.brick_length,
        refresh_ms=args.refresh_ms,
        project_root=project_root,
        bar_mode=args.bar_mode,
        range_ticks=args.range_ticks,
    )
    service.start()

    app = create_app(service, project_root)
    server = MultiServerThread(app, args.host, args.port)
    url = display_url(args.host, args.port)
    shutdown_requested = threading.Event()

    def request_shutdown(signum=None, frame=None) -> None:
        if shutdown_requested.is_set():
            return
        signal_name = signal.Signals(signum).name if signum is not None else "KeyboardInterrupt"
        print(f"收到退出信号: {signal_name}，正在关闭服务...")
        shutdown_requested.set()
        server.shutdown()

    for item in (signal.SIGINT, signal.SIGTERM):
        signal.signal(item, request_shutdown)

    print(f"数据源: {args.provider}")
    print("图表地址:")
    for item in listening_summary(args.host, args.port):
        print(f"  {item}")
    server.start()

    if args.open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        while server.is_alive() and not shutdown_requested.is_set():
            server.join(timeout=1)
    except KeyboardInterrupt:
        request_shutdown()
    finally:
        server.shutdown()
        service.stop()


if __name__ == "__main__":
    main()
