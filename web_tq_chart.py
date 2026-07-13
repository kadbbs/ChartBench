from __future__ import annotations

import argparse
import signal
import socket
import threading
import webbrowser

from werkzeug.serving import BaseWSGIServer, ThreadedWSGIServer, make_server

from tq_app.config_profiles import load_layered_env
from tq_app.cli.arguments import add_market_arguments
from tq_app.configuration.defaults import (
    DEFAULT_BAR_MODE,
    DEFAULT_BRICK_LENGTH,
    DEFAULT_DATA_LENGTH,
    DEFAULT_DURATION_SECONDS,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_PROVIDER,
    DEFAULT_RANGE_TICKS,
    DEFAULT_REFRESH_MS,
    DEFAULT_SYMBOL,
    env_default_int,
    env_default_str,
)
from tq_app.runtime import runtime_project_root
from tq_app.service import MarketDataService
from tq_app.web import create_app

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
    load_layered_env(runtime_project_root())
    chart_default_provider = env_default_str("TQ_CHART_DEFAULT_PROVIDER", env_default_str("TQ_DEFAULT_PROVIDER", DEFAULT_PROVIDER))
    chart_default_symbol = env_default_str("TQ_CHART_DEFAULT_SYMBOL", env_default_str("TQ_DEFAULT_SYMBOL", DEFAULT_SYMBOL))
    parser = argparse.ArgumentParser(description="行情浏览器图表工作台")
    add_market_arguments(
        parser,
        provider_default=chart_default_provider,
        provider_choices=["tianqin", "binance", "bitget"],
        symbol_default=chart_default_symbol,
        duration_default=env_default_int("TQ_DEFAULT_DURATION_SECONDS", DEFAULT_DURATION_SECONDS),
        data_length_default=env_default_int("TQ_DEFAULT_DATA_LENGTH", DEFAULT_DATA_LENGTH),
    )
    parser.add_argument("--brick-length", type=int, default=env_default_int("TQ_DEFAULT_BRICK_LENGTH", DEFAULT_BRICK_LENGTH), help="Range Bar / Renko 保留砖块数量")
    parser.add_argument("--refresh-ms", type=int, default=env_default_int("TQ_DEFAULT_REFRESH_MS", DEFAULT_REFRESH_MS), help="刷新间隔，单位毫秒")
    parser.add_argument(
        "--bar-mode",
        default=env_default_str("TQ_DEFAULT_BAR_MODE", DEFAULT_BAR_MODE),
        choices=["time", "tick", "range", "renko"],
        help="图表类型: time / tick / range / renko",
    )
    parser.add_argument(
        "--range-ticks",
        type=int,
        default=env_default_int("TQ_DEFAULT_RANGE_TICKS", DEFAULT_RANGE_TICKS),
        help="Range Bar / Renko 的价格跨度，单位 tick",
    )
    parser.add_argument("--host", default=env_default_str("TQ_DEFAULT_HOST", DEFAULT_HOST), help="监听地址")
    parser.add_argument("--port", type=int, default=env_default_int("TQ_DEFAULT_PORT", DEFAULT_PORT), help="监听端口")
    parser.add_argument("--open-browser", action="store_true", help="启动后自动打开浏览器")
    parser.add_argument(
        "--backtest-ui",
        action="store_true",
        help="显式启用回测研究工作台和后台回测任务。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = runtime_project_root()
    load_layered_env(project_root)
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

    backtest_manager = None
    if args.backtest_ui:
        from tq_app.backtesting.experiments import BacktestExperimentManager

        backtest_manager = BacktestExperimentManager(project_root)
    app = create_app(service, project_root, backtest_manager=backtest_manager)
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
    if backtest_manager is not None:
        print(f"回测工作台: {url}/backtests")
        if args.host.strip() in {"0.0.0.0", "::", ""}:
            print("提示: 当前监听所有网卡；请通过防火墙或反向代理限制回测接口访问。")
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
        if backtest_manager is not None:
            backtest_manager.shutdown()
        service.stop()


if __name__ == "__main__":
    main()
