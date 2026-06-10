from __future__ import annotations

import datetime as dt
import json
import math
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS


APP_DIR = Path(__file__).resolve().parent
CACHE_DIR = APP_DIR / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)

PORT = 8767
ETF_WATCHLIST = {
    "562500": "机器人ETF",
    "512760": "芯片ETF",
    "563230": "卫星ETF",
    "159326": "电力设备ETF",
}

app = Flask(__name__, static_folder=str(APP_DIR), static_url_path="")
CORS(app)


def today_key() -> str:
    return dt.date.today().strftime("%Y%m%d")


def cache_path(name: str) -> Path:
    return CACHE_DIR / f"{today_key()}_{name}.json"


def read_cache(name: str, force: bool = False) -> Any | None:
    path = cache_path(name)
    if force or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_cache(name: str, payload: Any) -> None:
    cache_path(name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def finite(value: Any, default: float = math.nan) -> float:
    try:
        if value is None or value == "":
            return default
        if isinstance(value, str):
            value = value.replace("%", "").replace(",", "").replace("亿", "").strip()
        result = float(value)
        return result if math.isfinite(result) else default
    except Exception:
        return default


def pct(value: float) -> float | None:
    return round(value, 4) if math.isfinite(value) else None


def clean_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: clean_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean_json(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def lan_ips() -> list[str]:
    ips = {"127.0.0.1"}
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("169.254."):
                ips.add(ip)
    except Exception:
        pass
    def rank(ip: str) -> tuple[int, str]:
        if ip.startswith(("192.168.", "10.")) or ip.startswith("172."):
            return (0, ip)
        if ip == "127.0.0.1":
            return (2, ip)
        return (1, ip)

    return sorted(ips, key=rank)


def fetch_ma120(code: str, is_etf: bool = False) -> float:
    import akshare as ak

    end = dt.date.today().strftime("%Y%m%d")
    start = (dt.date.today() - dt.timedelta(days=300)).strftime("%Y%m%d")
    if is_etf:
        try:
            df = ak.fund_etf_hist_em(symbol=code, period="daily", start_date=start, end_date=end, adjust="")
        except Exception:
            df = ak.fund_etf_hist_sina(symbol=code)
    else:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust="")
    if df.empty or "收盘" not in df.columns:
        return math.nan
    closes = pd.to_numeric(df["收盘"], errors="coerce").dropna()
    if len(closes) < 120:
        return math.nan
    return float(closes.tail(120).mean())


def etf_monitor(force: bool = False) -> dict[str, Any]:
    cached = read_cache("etf_monitor", force)
    if cached:
        return cached

    import akshare as ak

    last_error = None
    spot = None
    for attempt in range(3):
        try:
            spot = ak.fund_etf_spot_em()
            break
        except Exception as exc:
            last_error = exc
            time.sleep(1.2 * (attempt + 1))
    if spot is None:
        raise RuntimeError(f"ETF实时数据读取失败：{last_error}")

    rows = []
    for code, default_name in ETF_WATCHLIST.items():
        hit = spot[spot["代码"].astype(str).str.zfill(6) == code]
        item = hit.iloc[0].to_dict() if not hit.empty else {}
        price = finite(item.get("最新价"))
        try:
            ma120 = fetch_ma120(code, is_etf=True)
        except Exception:
            ma120 = math.nan
        buy_point = ma120 * 0.88 if math.isfinite(ma120) else math.nan
        deviation = (price - ma120) / ma120 * 100 if math.isfinite(price) and math.isfinite(ma120) else math.nan
        gap = (price - buy_point) / buy_point * 100 if math.isfinite(price) and math.isfinite(buy_point) else math.nan
        reached = math.isfinite(price) and math.isfinite(buy_point) and price <= buy_point
        status = "达到点金术买点" if reached else f"距离买点还差 {gap:.2f}%" if math.isfinite(gap) else "实时价已更新，MA120暂缺"
        rows.append({
            "code": code,
            "name": item.get("名称") or default_name,
            "price": pct(price),
            "ma120": pct(ma120),
            "deviation": pct(deviation),
            "buy_point": pct(buy_point),
            "status": status,
            "reached": reached,
            "add_price_1": pct(price * 0.9) if math.isfinite(price) else None,
            "add_price_2": pct(price * 0.8) if math.isfinite(price) else None,
            "take_profit_1": pct(price * 1.1) if math.isfinite(price) else None,
            "take_profit_2": pct(ma120 * 1.12) if math.isfinite(ma120) else None,
        })

    payload = {"updated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "rows": rows}
    write_cache("etf_monitor", payload)
    return payload


def score_stock(row: dict[str, Any]) -> int:
    pe = finite(row.get("pe"))
    dividend = finite(row.get("dividend_yield"))
    deviation = finite(row.get("deviation"))
    roe = finite(row.get("roe"))
    growth = finite(row.get("profit_growth"))
    return (
        (20 if pe <= 10 else 15 if pe <= 15 else 10 if pe < 20 else 0)
        + (20 if dividend >= 8 else 15 if dividend >= 5 else 10 if dividend > 3 else 0)
        + (20 if deviation <= -20 else 15 if deviation <= -15 else 10 if deviation <= -12 else 0)
        + (20 if roe >= 20 else 15 if roe >= 15 else 10 if roe > 10 else 0)
        + (20 if growth >= 20 else 15 if growth >= 10 else 10 if growth > 0 else 0)
    )


def finance_maps() -> tuple[dict[str, dict[str, Any]], dict[str, float]]:
    import akshare as ak

    current_year = dt.date.today().year - 1
    years = [current_year, current_year - 1, current_year - 2]
    finance: dict[str, dict[str, Any]] = {}
    for year in years:
        try:
            report = ak.stock_yjbb_em(date=f"{year}1231")
        except Exception:
            continue
        for item in report.to_dict("records"):
            code = str(item.get("股票代码", "")).zfill(6)
            if not code:
                continue
            finance.setdefault(code, {})[f"profit_{year}"] = finite(item.get("净利润-净利润"))
            if year == current_year:
                finance[code]["roe"] = finite(item.get("净资产收益率"))
                finance[code]["industry"] = item.get("所处行业") or "-"

    dividend: dict[str, float] = {}
    try:
        div_df = ak.stock_history_dividend()
        for item in div_df.to_dict("records"):
            code = str(item.get("代码", "")).zfill(6)
            dividend[code] = finite(item.get("平均股息")) / 10
    except Exception:
        pass
    return finance, dividend


def stock_rows(limit: int | None) -> list[dict[str, Any]]:
    import akshare as ak

    last_error = None
    spot = None
    source = "东方财富"
    for attempt in range(1):
        try:
            spot = ak.stock_zh_a_spot_em()
            break
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    if spot is None:
        try:
            spot = ak.stock_zh_a_spot()
            source = "新浪备用"
        except Exception as exc:
            raise RuntimeError(f"A股实时列表读取失败：东方财富错误={last_error}；新浪错误={exc}") from exc
    if limit:
        spot = spot.head(limit)

    if source == "新浪备用":
        rows = []
        for item in spot.to_dict("records"):
            code_digits = "".join(ch for ch in str(item.get("代码", "")) if ch.isdigit())
            rows.append({
                "code": code_digits[-6:].zfill(6),
                "name": item.get("名称"),
                "industry": "-",
                "price": finite(item.get("最新价")),
                "pe": math.nan,
                "dividend_yield": math.nan,
                "roe": math.nan,
                "market_cap": math.nan,
                "profit_growth": math.nan,
                "profit_ok": False,
                "is_st": "ST" in str(item.get("名称", "")).upper(),
                "_source": source,
            })
        return rows

    finance, dividend = finance_maps()
    current_year = dt.date.today().year - 1
    rows = []
    for item in spot.to_dict("records"):
        code_digits = "".join(ch for ch in str(item.get("代码", "")) if ch.isdigit())
        code = code_digits[-6:].zfill(6)
        price = finite(item.get("最新价"))
        fin = finance.get(code, {})
        p1 = finite(fin.get(f"profit_{current_year - 2}"))
        p2 = finite(fin.get(f"profit_{current_year - 1}"))
        p3 = finite(fin.get(f"profit_{current_year}"))
        growth = ((p3 - p2) / abs(p2) * 100) if p2 > 0 and math.isfinite(p3) else math.nan
        annual_dividend = dividend.get(code, math.nan)
        rows.append({
            "code": code,
            "name": item.get("名称"),
            "industry": fin.get("industry", "-"),
            "price": price,
            "pe": finite(item.get("市盈率-动态")),
            "dividend_yield": annual_dividend / price * 100 if price and math.isfinite(annual_dividend) else math.nan,
            "roe": fin.get("roe", math.nan),
            "market_cap": finite(item.get("总市值")) / 100000000,
            "profit_growth": growth,
            "profit_ok": p3 > p2 > p1 > 0 if all(math.isfinite(x) for x in [p1, p2, p3]) else False,
            "is_st": "ST" in str(item.get("名称", "")).upper(),
            "_source": source,
        })
    return rows


def add_ma120(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=6) as pool:
        future_map = {pool.submit(fetch_ma120, row["code"], False): row for row in rows}
        for future in as_completed(future_map):
            row = future_map[future]
            try:
                row["ma120"] = future.result()
            except Exception:
                row["ma120"] = math.nan
    return rows


def scan_stocks(force: bool = False, limit: int | None = 50) -> dict[str, Any]:
    cache_name = f"stock_scan_{limit or 'all'}"
    cached = read_cache(cache_name, force)
    if cached:
        return cached

    rows = stock_rows(limit)
    source = rows[0].get("_source", "AkShare") if rows else "AkShare"
    raw_count = len(rows)
    prefiltered = [
        row for row in rows
        if finite(row.get("pe")) < 20
        and finite(row.get("dividend_yield")) > 3
        and finite(row.get("roe")) > 10
        and finite(row.get("market_cap")) > 50
        and row.get("profit_ok")
        and not row.get("is_st")
    ]
    rows = add_ma120(prefiltered)
    result = []
    for row in rows:
        price = finite(row.get("price"))
        ma120 = finite(row.get("ma120"))
        row["deviation"] = (price - ma120) / ma120 * 100 if ma120 else math.nan
        if ma120 > 0 and price < ma120 * 0.88:
            row["score"] = score_stock(row)
            row["buy_price"] = price
            row["add_price_1"] = price * 0.9
            row["add_price_2"] = price * 0.8
            row["take_profit_1"] = price * 1.1
            row["take_profit_2"] = ma120 * 1.12
            result.append(row)

    result.sort(key=lambda x: x.get("score", 0), reverse=True)
    payload = {
        "updated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(result),
        "source": source,
        "note": "当前使用新浪备用实时源，缺少 PE、市值等字段，严格筛选可能没有结果。" if source == "新浪备用" else "",
        "checked": raw_count,
        "rows": result,
    }
    write_cache(cache_name, clean_json(payload))
    return payload


@app.get("/")
def index():
    return send_from_directory(APP_DIR, "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return send_from_directory(APP_DIR, "manifest.webmanifest")


@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "port": PORT,
        "urls": [f"http://{ip}:{PORT}" for ip in lan_ips()],
        "time": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.get("/api/etf")
def api_etf():
    try:
        force = request.args.get("force", "0") == "1"
        return jsonify(clean_json(etf_monitor(force)))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/scan")
def api_scan():
    try:
        force = request.args.get("force", "0") == "1"
        limit_raw = request.args.get("limit", "50").strip()
        limit = int(limit_raw) if limit_raw else None
        return jsonify(clean_json(scan_stocks(force=force, limit=limit)))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    print("点金术手机版已启动：")
    for url in [f"http://{ip}:{PORT}" for ip in lan_ips()]:
        print(f"  {url}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
