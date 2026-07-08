from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any

import pandas as pd

from .base import DataSource


TIANQIN_PROVIDER_NAME = "tianqin"
TIANQIN_MAX_KLINE_LENGTH = 8000
TIANQIN_MAX_DURATION_SECONDS = 86400
TIANQIN_READY_TIMEOUT_SECONDS = 30
TIANQIN_CONTRACT_CATALOG_TTL_SECONDS = 24 * 60 * 60
TIANQIN_CONTRACT_CATALOG_FAILURE_TTL_SECONDS = 30 * 60
TIANQIN_CONTRACT_CATALOG_WAIT_SECONDS = 2.0
TIANQIN_CONTRACT_CATALOG_PROCESS_TIMEOUT_SECONDS = 45.0
TIANQIN_CONTRACT_CATALOG_OUTPUT_MARKER = "__TIANQIN_CONTRACT_CATALOG__"
TIANQIN_NO_PROXY_HOSTS = (
    ".shinnytech.com",
    "shinnytech.com",
    "auth.shinnytech.com",
    "api.shinnytech.com",
    "files.shinnytech.com",
)
TIANQIN_DEFAULT_CONTRACTS: tuple[tuple[str, str], ...] = (
    ("KQ.m@SHFE.cu", "沪铜"),
    ("KQ.m@SHFE.al", "沪铝"),
    ("KQ.m@SHFE.zn", "沪锌"),
    ("KQ.m@SHFE.pb", "沪铅"),
    ("KQ.m@SHFE.ni", "沪镍"),
    ("KQ.m@SHFE.sn", "沪锡"),
    ("KQ.m@SHFE.ao", "氧化铝"),
    ("KQ.m@SHFE.au", "沪金"),
    ("KQ.m@SHFE.ag", "沪银"),
    ("KQ.m@SHFE.rb", "螺纹"),
    ("KQ.m@SHFE.hc", "热卷"),
    ("KQ.m@SHFE.ss", "不锈钢"),
    ("KQ.m@SHFE.fu", "燃油"),
    ("KQ.m@SHFE.bu", "沥青"),
    ("KQ.m@SHFE.ru", "橡胶"),
    ("KQ.m@SHFE.br", "合成胶"),
    ("KQ.m@SHFE.sp", "纸浆"),
    ("KQ.m@SHFE.wr", "线材"),
    ("KQ.m@DCE.i", "铁矿"),
    ("KQ.m@DCE.j", "焦炭"),
    ("KQ.m@DCE.jm", "焦煤"),
    ("KQ.m@DCE.l", "塑料"),
    ("KQ.m@DCE.v", "PVC"),
    ("KQ.m@DCE.pp", "PP"),
    ("KQ.m@DCE.eg", "乙二醇"),
    ("KQ.m@DCE.eb", "苯乙烯"),
    ("KQ.m@DCE.pg", "LPG"),
    ("KQ.m@DCE.lh", "生猪"),
    ("KQ.m@DCE.a", "豆一"),
    ("KQ.m@DCE.b", "豆二"),
    ("KQ.m@DCE.m", "豆粕"),
    ("KQ.m@DCE.y", "豆油"),
    ("KQ.m@DCE.p", "棕榈"),
    ("KQ.m@DCE.c", "玉米"),
    ("KQ.m@DCE.cs", "淀粉"),
    ("KQ.m@DCE.jd", "鸡蛋"),
    ("KQ.m@DCE.rr", "粳米"),
    ("KQ.m@DCE.fb", "纤板"),
    ("KQ.m@DCE.bb", "胶板"),
    ("KQ.m@DCE.lg", "原木"),
    ("KQ.m@CZCE.CF", "棉花"),
    ("KQ.m@CZCE.CY", "棉纱"),
    ("KQ.m@CZCE.SR", "白糖"),
    ("KQ.m@CZCE.TA", "PTA"),
    ("KQ.m@CZCE.OI", "菜油"),
    ("KQ.m@CZCE.RM", "菜粕"),
    ("KQ.m@CZCE.MA", "甲醇"),
    ("KQ.m@CZCE.FG", "玻璃"),
    ("KQ.m@CZCE.SA", "纯碱"),
    ("KQ.m@CZCE.UR", "尿素"),
    ("KQ.m@CZCE.PF", "短纤"),
    ("KQ.m@CZCE.PK", "花生"),
    ("KQ.m@CZCE.AP", "苹果"),
    ("KQ.m@CZCE.CJ", "红枣"),
    ("KQ.m@CZCE.PX", "PX"),
    ("KQ.m@CZCE.SH", "烧碱"),
    ("KQ.m@CZCE.PR", "瓶片"),
    ("KQ.m@CZCE.SF", "硅铁"),
    ("KQ.m@CZCE.SM", "锰硅"),
    ("KQ.m@INE.sc", "原油"),
    ("KQ.m@INE.lu", "低硫燃油"),
    ("KQ.m@INE.nr", "20号胶"),
    ("KQ.m@INE.bc", "国际铜"),
    ("KQ.m@INE.ec", "集运欧线"),
    ("KQ.m@GFEX.si", "工业硅"),
    ("KQ.m@GFEX.lc", "碳酸锂"),
    ("KQ.m@GFEX.ps", "多晶硅"),
    ("KQ.m@CFFEX.IF", "沪深300"),
    ("KQ.m@CFFEX.IH", "上证50"),
    ("KQ.m@CFFEX.IC", "中证500"),
    ("KQ.m@CFFEX.IM", "中证1000"),
    ("KQ.m@CFFEX.T", "10年国债"),
    ("KQ.m@CFFEX.TF", "5年国债"),
    ("KQ.m@CFFEX.TS", "2年国债"),
    ("KQ.m@CFFEX.TL", "30年国债"),
)
TIANQIN_DOMESTIC_FUTURE_EXCHANGES = {"SHFE", "DCE", "CZCE", "INE", "GFEX", "CFFEX"}
_CONTRACT_CATALOG_LOCK = threading.Lock()
_CONTRACT_CATALOG_CACHE: list[dict[str, Any]] = []
_CONTRACT_CATALOG_CACHE_AT = 0.0
_CONTRACT_CATALOG_ERROR: str | None = None
_CONTRACT_CATALOG_ERROR_AT = 0.0
_CONTRACT_CATALOG_REFRESH_PROCESS: subprocess.Popen[str] | None = None
_CONTRACT_CATALOG_REFRESH_STARTED_AT = 0.0


def load_tianqin_contract_catalog(project_root: Any = None) -> list[dict[str, Any]]:
    root = Path(project_root) if project_root is not None else Path.cwd()
    _collect_contract_catalog_process()
    configured_symbols = _configured_symbols()
    if configured_symbols is not None:
        return _sort_tianqin_contracts([_tianqin_contract(symbol) for symbol in configured_symbols])

    base_contracts = [_tianqin_contract(symbol) for symbol in _default_catalog_symbols()]
    dynamic_contracts = _cached_contract_catalog()
    refresh_started = _maybe_start_contract_catalog_refresh(root)
    if refresh_started and not dynamic_contracts:
        wait_seconds = _contract_catalog_wait_seconds()
        if wait_seconds > 0:
            _wait_for_contract_catalog_refresh(wait_seconds)
            dynamic_contracts = _cached_contract_catalog()
    return _sort_tianqin_contracts(_merge_contracts(base_contracts, dynamic_contracts))


def load_tianqin_account_summary(project_root: Any = None) -> dict[str, Any]:
    del project_root
    username, password = _configured_auth()
    if not username or not password:
        return {"exchange": "TIANQIN", "configured": False}
    return {"exchange": "TIANQIN", "configured": True, "username": username}


def _configured_symbols() -> list[str] | None:
    raw = os.getenv("TIANQIN_SYMBOLS", "").strip()
    if not raw:
        return None
    symbols = [item.strip() for item in raw.split(",") if item.strip()]
    return list(dict.fromkeys(symbols)) or None


def _default_catalog_symbols() -> list[str]:
    preferred = (
        os.getenv("TQ_CHART_DEFAULT_SYMBOL", "").strip()
        or os.getenv("TIANQIN_DEFAULT_SYMBOL", "").strip()
        or TIANQIN_DEFAULT_CONTRACTS[0][0]
    )
    symbols = [preferred, *(symbol for symbol, _short_name in TIANQIN_DEFAULT_CONTRACTS)]
    return list(dict.fromkeys(symbols)) or [TIANQIN_DEFAULT_CONTRACTS[0][0]]


def _tianqin_contract(symbol: str) -> dict[str, Any]:
    exchange_id, variety_id = _parse_tianqin_symbol(symbol)
    short_name = TIANQIN_SHORT_NAME_BY_KEY.get((exchange_id, variety_id), symbol)
    is_main_continuous = symbol.startswith("KQ.m@")
    product_id = "主连" if is_main_continuous else "期货"
    display_name = f"{short_name}主连" if is_main_continuous and short_name != symbol else short_name
    label = f"{display_name} · {symbol}" if short_name != symbol else f"{symbol} · TIANQIN"
    return {
        "symbol": symbol,
        "name": display_name,
        "label": label,
        "exchange_id": exchange_id or "TIANQIN",
        "product_id": product_id,
        "short_name": short_name,
        "variety_id": variety_id,
    }


def _parse_tianqin_symbol(symbol: str) -> tuple[str, str]:
    raw = symbol.strip()
    if raw.startswith("KQ.m@"):
        raw = raw.split("@", 1)[1]
    if "." not in raw:
        return "TIANQIN", raw.lower()
    exchange, contract = raw.split(".", 1)
    variety = "".join(char for char in contract if char.isalpha())
    return exchange.upper(), variety.lower()


def _symbol_lookup_key(symbol: str) -> tuple[str, str]:
    return _parse_tianqin_symbol(symbol)


TIANQIN_SHORT_NAME_BY_KEY = {
    _symbol_lookup_key(symbol): short_name for symbol, short_name in TIANQIN_DEFAULT_CONTRACTS
}
TIANQIN_VARIETY_ORDER_BY_KEY = {
    _symbol_lookup_key(symbol): index for index, (symbol, _short_name) in enumerate(TIANQIN_DEFAULT_CONTRACTS)
}


def _cached_contract_catalog() -> list[dict[str, Any]]:
    with _CONTRACT_CATALOG_LOCK:
        return [dict(item) for item in _CONTRACT_CATALOG_CACHE]


def _merge_contracts(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for contract in group:
            symbol = str(contract.get("symbol") or "").strip()
            if symbol and symbol not in merged:
                merged[symbol] = dict(contract)
    return list(merged.values())


def _sort_tianqin_contracts(contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(contracts, key=_tianqin_contract_sort_key)


def _tianqin_contract_sort_key(contract: dict[str, Any]) -> tuple[Any, ...]:
    symbol = str(contract.get("symbol") or "")
    exchange_id, variety_id = _parse_tianqin_symbol(symbol)
    variety_order = TIANQIN_VARIETY_ORDER_BY_KEY.get((exchange_id, variety_id), 9999)
    is_main_continuous = 0 if symbol.startswith("KQ.m@") else 1
    return (
        variety_order,
        exchange_id,
        variety_id,
        is_main_continuous,
        _delivery_sort_value(symbol),
        symbol,
    )


def _delivery_sort_value(symbol: str) -> int:
    raw = symbol.split(".", 1)[1] if "." in symbol else symbol
    digits = "".join(char for char in raw if char.isdigit())
    return int(digits) if digits else -1


def _maybe_start_contract_catalog_refresh(project_root: Path) -> bool:
    global _CONTRACT_CATALOG_ERROR
    global _CONTRACT_CATALOG_ERROR_AT
    global _CONTRACT_CATALOG_REFRESH_PROCESS
    global _CONTRACT_CATALOG_REFRESH_STARTED_AT
    _collect_contract_catalog_process()
    if not _env_bool("TIANQIN_INCLUDE_FUTURE_CONTRACTS", default=True):
        return False
    username, password = _configured_auth()
    if not username or not password:
        return False

    now = time.monotonic()
    ttl_seconds = _env_float("TIANQIN_CONTRACT_CATALOG_TTL_SECONDS", TIANQIN_CONTRACT_CATALOG_TTL_SECONDS)
    failure_ttl_seconds = _env_float(
        "TIANQIN_CONTRACT_CATALOG_FAILURE_TTL_SECONDS",
        TIANQIN_CONTRACT_CATALOG_FAILURE_TTL_SECONDS,
    )
    with _CONTRACT_CATALOG_LOCK:
        if _CONTRACT_CATALOG_CACHE and now - _CONTRACT_CATALOG_CACHE_AT < ttl_seconds:
            return False
        if _CONTRACT_CATALOG_REFRESH_PROCESS is not None:
            return False
        if _CONTRACT_CATALOG_ERROR_AT and now - _CONTRACT_CATALOG_ERROR_AT < failure_ttl_seconds:
            return False
        command = _contract_catalog_command(project_root)
        env = os.environ.copy()
        try:
            _CONTRACT_CATALOG_REFRESH_PROCESS = subprocess.Popen(
                command,
                cwd=str(project_root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
        except Exception as exc:
            _CONTRACT_CATALOG_ERROR = f"启动天勤量化合约目录查询失败: {exc}"
            _CONTRACT_CATALOG_ERROR_AT = now
            return False
        _CONTRACT_CATALOG_REFRESH_STARTED_AT = now
        return True


def _contract_catalog_command(project_root: Path) -> list[str]:
    code = (
        "from pathlib import Path\n"
        "import json\n"
        "from tq_app.config_profiles import load_layered_env\n"
        "from tq_app.data_sources.tianqin import "
        "_fetch_tianqin_future_contracts, TIANQIN_CONTRACT_CATALOG_OUTPUT_MARKER\n"
        f"root = Path({str(project_root)!r})\n"
        "load_layered_env(root)\n"
        "contracts = _fetch_tianqin_future_contracts()\n"
        "symbols = [item['symbol'] for item in contracts]\n"
        "print(TIANQIN_CONTRACT_CATALOG_OUTPUT_MARKER + json.dumps(symbols, ensure_ascii=False), flush=True)\n"
    )
    return [sys.executable, "-c", code]


def _wait_for_contract_catalog_refresh(timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        _collect_contract_catalog_process()
        with _CONTRACT_CATALOG_LOCK:
            if _CONTRACT_CATALOG_REFRESH_PROCESS is None:
                return
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    _collect_contract_catalog_process()


def _collect_contract_catalog_process() -> None:
    global _CONTRACT_CATALOG_REFRESH_PROCESS
    global _CONTRACT_CATALOG_REFRESH_STARTED_AT
    with _CONTRACT_CATALOG_LOCK:
        process = _CONTRACT_CATALOG_REFRESH_PROCESS
        started_at = _CONTRACT_CATALOG_REFRESH_STARTED_AT
    if process is None:
        return

    timeout_seconds = _env_float(
        "TIANQIN_CONTRACT_CATALOG_PROCESS_TIMEOUT_SECONDS",
        TIANQIN_CONTRACT_CATALOG_PROCESS_TIMEOUT_SECONDS,
    )
    if process.poll() is None:
        if time.monotonic() - started_at <= timeout_seconds:
            return
        try:
            process.kill()
        except Exception:
            pass
        stdout, stderr = _communicate_contract_catalog_process(process)
        _finish_contract_catalog_process(
            process,
            contracts=None,
            error=_contract_catalog_error_message(stdout, stderr, process.returncode, timed_out=True),
        )
        return

    stdout, stderr = _communicate_contract_catalog_process(process)
    try:
        contracts = _parse_contract_catalog_output(stdout) if process.returncode == 0 else []
        if not contracts:
            raise RuntimeError(_contract_catalog_error_message(stdout, stderr, process.returncode))
    except Exception as exc:
        _finish_contract_catalog_process(process, contracts=None, error=str(exc))
        return
    _finish_contract_catalog_process(process, contracts=contracts, error=None)


def _communicate_contract_catalog_process(process: subprocess.Popen[str]) -> tuple[str, str]:
    try:
        return process.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except Exception:
            pass
        try:
            return process.communicate(timeout=1)
        except Exception:
            return "", ""


def _finish_contract_catalog_process(
    process: subprocess.Popen[str],
    *,
    contracts: list[dict[str, Any]] | None,
    error: str | None,
) -> None:
    global _CONTRACT_CATALOG_CACHE
    global _CONTRACT_CATALOG_CACHE_AT
    global _CONTRACT_CATALOG_ERROR
    global _CONTRACT_CATALOG_ERROR_AT
    global _CONTRACT_CATALOG_REFRESH_PROCESS
    global _CONTRACT_CATALOG_REFRESH_STARTED_AT
    with _CONTRACT_CATALOG_LOCK:
        if _CONTRACT_CATALOG_REFRESH_PROCESS is not process:
            return
        if contracts is not None:
            _CONTRACT_CATALOG_CACHE = contracts
            _CONTRACT_CATALOG_CACHE_AT = time.monotonic()
            _CONTRACT_CATALOG_ERROR = None
            _CONTRACT_CATALOG_ERROR_AT = 0.0
        elif error:
            _CONTRACT_CATALOG_ERROR = error
            _CONTRACT_CATALOG_ERROR_AT = time.monotonic()
        _CONTRACT_CATALOG_REFRESH_PROCESS = None
        _CONTRACT_CATALOG_REFRESH_STARTED_AT = 0.0


def _parse_contract_catalog_output(stdout: str) -> list[dict[str, Any]]:
    payload = ""
    for line in reversed(stdout.splitlines()):
        if line.startswith(TIANQIN_CONTRACT_CATALOG_OUTPUT_MARKER):
            payload = line[len(TIANQIN_CONTRACT_CATALOG_OUTPUT_MARKER):]
            break
    if not payload:
        raise RuntimeError("天勤量化未返回未到期期货合约列表。")
    raw_symbols = json.loads(payload)
    if not isinstance(raw_symbols, list):
        raise RuntimeError("天勤量化合约目录返回格式不正确。")
    contracts: list[dict[str, Any]] = []
    for raw_symbol in raw_symbols:
        if isinstance(raw_symbol, dict):
            symbol = str(raw_symbol.get("symbol") or "").strip()
        else:
            symbol = str(raw_symbol or "").strip()
        if symbol:
            contracts.append(_tianqin_contract(symbol))
    if not contracts:
        raise RuntimeError("天勤量化未返回未到期期货合约列表。")
    return _sort_tianqin_contracts(_merge_contracts(contracts))


def _contract_catalog_error_message(
    stdout: str,
    stderr: str,
    returncode: int | None,
    *,
    timed_out: bool = False,
) -> str:
    if timed_out:
        timeout_seconds = _env_float(
            "TIANQIN_CONTRACT_CATALOG_PROCESS_TIMEOUT_SECONDS",
            TIANQIN_CONTRACT_CATALOG_PROCESS_TIMEOUT_SECONDS,
        )
        return f"天勤量化合约目录查询超过 {timeout_seconds:g} 秒。"
    detail = (stderr or stdout or "").strip()
    if detail:
        return detail[-800:]
    return f"天勤量化合约目录查询失败，退出码: {returncode}"


def _fetch_tianqin_future_contracts() -> list[dict[str, Any]]:
    api: Any | None = None
    try:
        api = _create_api()
        symbols = [str(symbol).strip() for symbol in api.query_quotes(ins_class="FUTURE", expired=False)]
        contracts = [
            _tianqin_contract(symbol)
            for symbol in symbols
            if symbol and _is_domestic_future_symbol(symbol)
        ]
        return _sort_tianqin_contracts(_merge_contracts(contracts))
    finally:
        if api is not None:
            try:
                api.close()
            except Exception:
                pass


def _is_domestic_future_symbol(symbol: str) -> bool:
    exchange_id, variety_id = _parse_tianqin_symbol(symbol)
    return exchange_id in TIANQIN_DOMESTIC_FUTURE_EXCHANGES and bool(variety_id)


def _contract_catalog_wait_seconds() -> float:
    return _env_float("TIANQIN_CONTRACT_CATALOG_WAIT_SECONDS", TIANQIN_CONTRACT_CATALOG_WAIT_SECONDS)


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def _configured_auth() -> tuple[str, str]:
    username = (
        os.getenv("TIANQIN_USERNAME", "").strip()
        or os.getenv("TQSDK_USERNAME", "").strip()
        or os.getenv("KQ_USERNAME", "").strip()
    )
    password = (
        os.getenv("TIANQIN_PASSWORD", "").strip()
        or os.getenv("TQSDK_PASSWORD", "").strip()
        or os.getenv("KQ_PASSWORD", "").strip()
    )
    return username, password


def _import_tqsdk() -> tuple[Any, Any]:
    try:
        from tqsdk import TqApi, TqAuth
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少天勤量化依赖：请先安装 tqsdk，或运行 pip install -r requirements.txt。") from exc
    return TqApi, TqAuth


def _create_api() -> Any:
    TqApi, TqAuth = _import_tqsdk()
    _ensure_tianqin_no_proxy()
    username, password = _configured_auth()
    if not username or not password:
        raise RuntimeError("缺少天勤量化账号配置：请在 .env 设置 TIANQIN_USERNAME / TIANQIN_PASSWORD。")
    auth = TqAuth(username, password)
    try:
        auth.init(mode="real")
        auth.login()
    except Exception as exc:
        raise RuntimeError(f"天勤量化账号鉴权失败或网络不可用: {exc}") from exc
    auth.login = lambda: None
    return TqApi(auth=auth, disable_print=True)


def _ensure_tianqin_no_proxy() -> None:
    for env_name in ("NO_PROXY", "no_proxy"):
        current_items = [
            item.strip()
            for item in os.getenv(env_name, "").split(",")
            if item.strip()
        ]
        existing = {item.lower() for item in current_items}
        missing = [item for item in TIANQIN_NO_PROXY_HOSTS if item.lower() not in existing]
        if missing:
            os.environ[env_name] = ",".join([*current_items, *missing])


def _wait_update(api: Any, timeout_seconds: float = 1.0) -> bool:
    try:
        return bool(api.wait_update(deadline=time.time() + timeout_seconds))
    except TypeError:
        return bool(api.wait_update())


def _serial_ready(api: Any, serial: pd.DataFrame) -> bool:
    try:
        return bool(api.is_serial_ready(serial))
    except Exception:
        return not serial_to_frame(serial).empty


def _serial_changed(api: Any, serial: pd.DataFrame) -> bool:
    try:
        return bool(api.is_changing(serial))
    except Exception:
        return True


def serial_to_frame(serial: pd.DataFrame) -> pd.DataFrame:
    if serial is None or serial.empty or "datetime" not in serial.columns:
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
    frame = serial.copy()
    for column in ["datetime", "open", "high", "low", "close", "volume"]:
        if column not in frame.columns:
            frame[column] = pd.NA
    frame["timestamp_ns"] = pd.to_numeric(frame["datetime"], errors="coerce")
    frame = frame.dropna(subset=["timestamp_ns", "open", "high", "low", "close"])
    frame = frame[frame["timestamp_ns"] > 0]
    if frame.empty:
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
    frame["datetime"] = pd.to_datetime(frame["timestamp_ns"].astype("int64"), unit="ns", utc=True, errors="coerce")
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["datetime", "open", "high", "low", "close"])
    frame = frame.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")
    return frame[["datetime", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


class TianqinDataSource(DataSource):
    provider_name = TIANQIN_PROVIDER_NAME

    def __init__(
        self,
        symbol: str,
        duration_seconds: int,
        data_length: int,
        brick_length: int,
        refresh_ms: int,
        bar_mode: str,
        range_ticks: int,
    ) -> None:
        self.symbol = symbol.strip()
        self.duration_seconds = int(duration_seconds)
        self.data_length = min(max(int(data_length), 1), TIANQIN_MAX_KLINE_LENGTH)
        self.brick_length = brick_length
        self.refresh_ms = refresh_ms
        self.bar_mode = bar_mode
        self.range_ticks = range_ticks
        self.product_type = "TQSDK"
        self.kline_type = "MARKET"
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._ready = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._bars: pd.DataFrame | None = None
        self._error: str | None = None
        self._version = 0
        self._last_refresh_at = 0.0
        self._last_update_at: float | None = None
        self._last_message_at: float | None = None
        self._last_kline_at: float | None = None
        self._last_ticker_price: float | None = None
        self._last_ticker_ts: int | None = None
        self._last_frame_signature: tuple[Any, ...] | None = None
        self._stream_state = "starting"

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            if self._bars is None or self._bars.empty:
                self._ready.clear()
            self._error = None
            self._stream_state = "starting"
        self._thread = threading.Thread(target=self._run, name=f"tianqin-{self.symbol}-{self.duration_seconds}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def wait_for_update(self, last_version: int | None, timeout: float) -> int:
        self.start()
        self._ready.wait(timeout=TIANQIN_READY_TIMEOUT_SECONDS)
        with self._condition:
            if last_version is None or self._version != last_version:
                return self._version
            self._condition.wait_for(lambda: self._version != last_version or self._stop_event.is_set(), timeout=timeout)
            return self._version

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_locked()

    def get_bars(self) -> pd.DataFrame:
        frame, _status = self.get_bars_with_status()
        return frame

    def get_bars_with_status(self) -> tuple[pd.DataFrame, dict[str, Any]]:
        self.start()
        self._ready.wait(timeout=TIANQIN_READY_TIMEOUT_SECONDS)
        with self._lock:
            if self._bars is not None and not self._bars.empty:
                return self._bars.copy(), self._status_locked()
            if self._error:
                raise RuntimeError(self._error)
            raise RuntimeError("天勤量化数据源尚未就绪，请确认账号、合约代码和网络连接。")

    def _status_locked(self) -> dict[str, Any]:
        return {
            "version": self._version,
            "last_refresh_at": self._last_refresh_at or None,
            "last_update_at": self._last_update_at,
            "last_message_at": self._last_message_at,
            "last_kline_at": self._last_kline_at,
            "stream_state": self._stream_state,
            "stream_url": "tqsdk://wait_update",
            "product_type": self.product_type,
            "kline_type": self.kline_type,
            "last_ticker_price": self._last_ticker_price,
            "last_ticker_ts": self._last_ticker_ts,
            "error": self._error,
        }

    def _run(self) -> None:
        api: Any | None = None
        try:
            if self.bar_mode != "time":
                raise RuntimeError("天勤量化数据源当前只支持时间 K 线。")
            if self.duration_seconds <= 0 or self.duration_seconds > TIANQIN_MAX_DURATION_SECONDS:
                raise RuntimeError(f"天勤量化 K 线周期必须在 1 到 {TIANQIN_MAX_DURATION_SECONDS} 秒之间。")
            with self._lock:
                self._stream_state = "connecting"
                self._condition.notify_all()
            api = _create_api()
            serial = api.get_kline_serial(self.symbol, self.duration_seconds, data_length=self.data_length)
            while not self._stop_event.is_set():
                updated = _wait_update(api, timeout_seconds=1.0)
                if not updated and self._bars is not None:
                    continue
                if self._bars is not None and not _serial_changed(api, serial):
                    continue
                frame = serial_to_frame(serial.tail(self.data_length))
                if frame.empty:
                    continue
                if not _serial_ready(api, serial) and self._bars is None:
                    continue
                with self._lock:
                    signature = self._frame_signature(frame)
                    if signature == self._last_frame_signature:
                        continue
                    self._commit_bars_locked(frame)
        except Exception as exc:
            with self._lock:
                self._error = str(exc)
                self._stream_state = "stopped_error"
                self._condition.notify_all()
            self._ready.set()
        finally:
            if api is not None:
                try:
                    api.close()
                except Exception:
                    pass
            with self._lock:
                if self._stream_state != "stopped_error":
                    self._stream_state = "stopped" if self._stop_event.is_set() else self._stream_state
                self._condition.notify_all()

    def _commit_bars_locked(self, frame: pd.DataFrame) -> None:
        frame = frame.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")
        self._bars = frame.tail(self.data_length).reset_index(drop=True)
        self._last_frame_signature = self._frame_signature(self._bars)
        self._error = None
        self._version += 1
        self._last_message_at = time.time()
        self._last_update_at = self._last_message_at
        self._last_kline_at = self._last_message_at
        self._last_refresh_at = time.monotonic()
        self._stream_state = "live"
        if self._bars is not None and not self._bars.empty:
            last = self._bars.iloc[-1]
            self._last_ticker_price = float(last["close"])
            self._last_ticker_ts = int(pd.Timestamp(last["datetime"]).timestamp() * 1000)
        self._condition.notify_all()
        self._ready.set()

    @staticmethod
    def _frame_signature(frame: pd.DataFrame) -> tuple[Any, ...] | None:
        if frame.empty:
            return None
        last = frame.iloc[-1]
        timestamp = pd.Timestamp(last["datetime"]).value
        return (
            len(frame),
            int(timestamp),
            float(last["open"]),
            float(last["high"]),
            float(last["low"]),
            float(last["close"]),
            float(last.get("volume", 0) or 0),
        )
