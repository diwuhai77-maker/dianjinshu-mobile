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


def is_mainland_stock_code(code: str) -> bool:
    return len(code) == 6 and code.isdigit() and code.startswith(("00", "30", "60", "68"))


def first_column(df: pd.DataFrame, *names: str, contains: tuple[str, ...] | None = None) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    if contains:
        for column in df.columns:
            text = str(column)
            if all(part in text for part in contains):
                return column
    return None


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
        if ip.startswith(("192.168.", "10.", "172.")):
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
    close_col = first_column(df, "收盘", "close", contains=("收", "盘"))
    if df.empty or not close_col:
        return math.nan
    closes = pd.to_numeric(df[close_col], errors="coerce").dropna()
    if len(closes) < 120:
        return math.nan
    return float(closes.tail(120).mean())


def fetch_price_ma120(code: str) -> tuple[float, float]:
    import akshare as ak

    end = dt.date.today().strftime("%Y%m%d")
    start = (dt.date.today() - dt.timedelta(days=300)).strftime("%Y%m%d")
    try:
        prefix = "sh" if code.startswith(("60", "68")) else "sz"
        df = ak.stock_zh_a_daily(symbol=f"{prefix}{code}", adjust="")
    except Exception:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust="")
    close_col = first_column(df, "收盘", "close", contains=("收", "盘"))
    if df.empty or not close_col:
        return math.nan, math.nan
    closes = pd.to_numeric(df[close_col], errors="coerce").dropna()
    if closes.empty:
        return math.nan, math.nan
    price = float(closes.iloc[-1])
    ma120 = float(closes.tail(120).mean()) if len(closes) >= 120 else math.nan
    return price, ma120


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
        raise RuntimeError(f"ETF 实时数据读取失败：{last_error}")

    code_col = first_column(spot, "代码")
    name_col = first_column(spot, "名称")
    price_col = first_column(spot, "最新价", "现价")
    rows = []
    for code, default_name in ETF_WATCHLIST.items():
        hit = spot[spot[code_col].astype(str).str.zfill(6) == code] if code_col else pd.DataFrame()
        item = hit.iloc[0].to_dict() if not hit.empty else {}
        price = finite(item.get(price_col)) if price_col else math.nan
        try:
            ma120 = fetch_ma120(code, is_etf=True)
        except Exception:
            ma120 = math.nan
        buy_point = ma120 * 0.88 if math.isfinite(ma120) else math.nan
        deviation = (price - ma120) / ma120 * 100 if math.isfinite(price) and math.isfinite(ma120) else math.nan
        gap = (price - buy_point) / buy_point * 100 if math.isfinite(price) and math.isfinite(buy_point) else math.nan
        reached = math.isfinite(price) and math.isfinite(buy_point) and price <= buy_point
        status = "达到点金术买点" if reached else f"距离买点还差 {gap:.2f}%" if math.isfinite(gap) else "实时价已更新，MA120 暂缺"
        rows.append({
            "code": code,
            "name": item.get(name_col) if name_col else default_name,
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
    finance: dict[str, dict[str, Any]] = {}
    try:
        report = ak.stock_yjbb_em(date=f"{current_year}1231")
    except Exception:
        report = pd.DataFrame()

    if not report.empty:
        code_col = first_column(report, "股票代码", "代码")
        name_col = first_column(report, "股票简称", "名称")
        profit_col = first_column(report, "净利润-净利润", contains=("净利润",))
        roe_col = first_column(report, "净资产收益率", contains=("净资产收益率",))
        industry_col = first_column(report, "所处行业", "行业")
        growth_col = first_column(report, "净利润-同比增长", contains=("净利润", "同比"))
        for item in report.to_dict("records"):
            code = str(item.get(code_col, "")).zfill(6) if code_col else ""
            if len(code) != 6 or not code.isdigit():
                continue
            finance[code] = {
                f"profit_{current_year}": finite(item.get(profit_col)) if profit_col else math.nan,
                "roe": finite(item.get(roe_col)) if roe_col else math.nan,
                "industry": item.get(industry_col) if industry_col else "-",
                "name": item.get(name_col) if name_col else code,
                "profit_growth": finite(item.get(growth_col)) if growth_col else math.nan,
            }

    dividend: dict[str, float] = {}
    try:
        div_df = ak.stock_history_dividend()
    except Exception:
        div_df = pd.DataFrame()
    if not div_df.empty:
        code_col = first_column(div_df, "代码", "股票代码")
        dividend_col = first_column(div_df, "年均股息", "平均股息", contains=("股息",))
        for item in div_df.to_dict("records"):
            code = str(item.get(code_col, "")).zfill(6) if code_col else ""
            annual_dividend = finite(item.get(dividend_col)) if dividend_col else math.nan
            if len(code) == 6 and code.isdigit() and math.isfinite(annual_dividend) and annual_dividend > 0:
                dividend[code] = annual_dividend / 10
    return finance, dividend


def stock_rows(limit: int | None) -> list[dict[str, Any]]:
    import akshare as ak

    spot = None
    source = "东方财富"
    try:
        spot = ak.stock_zh_a_spot_em()
    except Exception:
        try:
            spot = ak.stock_zh_a_spot()
            source = "新浪备用"
        except Exception:
            spot = None

    finance, dividend = finance_maps()
    current_year = dt.date.today().year - 1
    if spot is None or spot.empty:
        rows = []
        for code, fin in finance.items():
            profit = finite(fin.get(f"profit_{current_year}"))
            growth = finite(fin.get("profit_growth"))
            rows.append({
                "code": code,
                "name": fin.get("name", code),
                "industry": fin.get("industry", "-"),
                "price": math.nan,
                "pe": math.nan,
                "dividend_yield": math.nan,
                "annual_dividend": dividend.get(code, math.nan),
                "roe": fin.get("roe", math.nan),
                "market_cap": math.nan,
                "profit_growth": growth,
                "profit_ok": profit > 0 and growth > 0 if math.isfinite(profit) and math.isfinite(growth) else False,
                "is_st": "ST" in str(fin.get("name", "")).upper(),
                "_source": "基本面备用",
            })
        return rows[:limit] if limit else rows

    if limit:
        spot = spot.head(limit)

    code_col = first_column(spot, "代码")
    name_col = first_column(spot, "名称")
    price_col = first_column(spot, "最新价", "现价")
    pe_col = first_column(spot, "市盈率-动态", "市盈率")
    market_cap_col = first_column(spot, "总市值", "总市值-亿")
    rows = []
    for item in spot.to_dict("records"):
        code_digits = "".join(ch for ch in str(item.get(code_col, "")) if ch.isdigit()) if code_col else ""
        code = code_digits[-6:].zfill(6)
        if len(code) != 6 or not code.isdigit():
            continue
        price = finite(item.get(price_col)) if price_col else math.nan
        fin = finance.get(code, {})
        profit = finite(fin.get(f"profit_{current_year}"))
        growth = finite(fin.get("profit_growth"))
        annual_dividend = dividend.get(code, math.nan)
        market_cap = finite(item.get(market_cap_col)) if market_cap_col else math.nan
        if math.isfinite(market_cap) and market_cap > 1000000:
            market_cap = market_cap / 100000000
        rows.append({
            "code": code,
            "name": item.get(name_col) if name_col else fin.get("name", code),
            "industry": fin.get("industry", "-"),
            "price": price,
            "pe": finite(item.get(pe_col)) if pe_col else math.nan,
            "dividend_yield": annual_dividend / price * 100 if price and math.isfinite(annual_dividend) else math.nan,
            "annual_dividend": annual_dividend,
            "roe": fin.get("roe", math.nan),
            "market_cap": market_cap,
            "profit_growth": growth,
            "profit_ok": profit > 0 and growth > 0 if math.isfinite(profit) and math.isfinite(growth) else False,
            "is_st": "ST" in str(item.get(name_col, "")).upper() if name_col else False,
            "_source": source,
        })
    return rows


def add_ma120(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=6) as pool:
        future_map = {pool.submit(fetch_price_ma120, row["code"]): row for row in rows}
        for future in as_completed(future_map):
            row = future_map[future]
            try:
                price, ma120 = future.result()
                if not math.isfinite(finite(row.get("price"))) and math.isfinite(price):
                    row["price"] = price
                row["ma120"] = ma120
                annual_dividend = finite(row.get("annual_dividend"))
                current_price = finite(row.get("price"))
                if not math.isfinite(finite(row.get("dividend_yield"))) and current_price > 0 and math.isfinite(annual_dividend):
                    row["dividend_yield"] = annual_dividend / current_price * 100
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

    strict_source = source == "东方财富"
    if strict_source:
        prefiltered = [
            row for row in rows
            if finite(row.get("pe")) < 20
            and finite(row.get("dividend_yield")) > 3
            and finite(row.get("roe")) > 10
            and finite(row.get("market_cap")) > 50
            and row.get("profit_ok")
            and not row.get("is_st")
        ]
    else:
        prefiltered = [
            row for row in rows
            if is_mainland_stock_code(str(row.get("code", "")))
            and finite(row.get("roe")) > 10
            and row.get("profit_ok")
            and not row.get("is_st")
        ]
        prefiltered.sort(
            key=lambda row: (
                finite(row.get("annual_dividend"), 0),
                finite(row.get("roe"), 0),
                finite(row.get("profit_growth"), 0),
            ),
            reverse=True,
        )
        prefiltered = prefiltered[:80]

    rows = add_ma120(prefiltered)
    result = []
    watch_rows = []
    for row in rows:
        price = finite(row.get("price"))
        ma120 = finite(row.get("ma120"))
        if not math.isfinite(price) or not math.isfinite(ma120) or ma120 <= 0:
            continue
        dividend_known = math.isfinite(finite(row.get("dividend_yield")))
        dividend_ok = finite(row.get("dividend_yield")) > 3 if strict_source else True
        if not dividend_ok:
            continue
        row["deviation"] = (price - ma120) / ma120 * 100
        row["score"] = score_stock(row)
        row["buy_price"] = price
        row["add_price_1"] = price * 0.9
        row["add_price_2"] = price * 0.8
        row["take_profit_1"] = price * 1.1
        row["take_profit_2"] = ma120 * 1.12
        row["distance_to_buy"] = (price - ma120 * 0.88) / (ma120 * 0.88) * 100
        if price < ma120 * 0.88 and (dividend_known or not strict_source):
            row["candidate_type"] = "买点候选"
            row["reason"] = "低于 MA120 的 88%，符合点金术价格买点"
            result.append(row)
        else:
            row["candidate_type"] = "观察候选"
            row["reason"] = "基本面通过，等待价格进一步靠近买点"
            watch_rows.append(row)

    result.sort(key=lambda x: x.get("score", 0), reverse=True)
    watch_rows.sort(key=lambda x: finite(x.get("distance_to_buy"), 999))
    display_rows = result if result else watch_rows[:20]
    note = ""
    if source != "东方财富":
        note = "当前实时源降级：PE、市值或股息字段可能缺失，已用 ROE、利润增长、MA120 生成观察候选；显示买点候选时仍按 MA120 价格规则判断。"
    payload = {
        "updated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(display_rows),
        "strict_count": len(result),
        "source": source,
        "note": note,
        "checked": raw_count,
        "rows": display_rows,
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
