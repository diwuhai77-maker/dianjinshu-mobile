from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import app


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "public" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def safe_call(name: str, func, *args, **kwargs) -> dict[str, Any]:
    try:
        payload = func(*args, **kwargs)
        return {"ok": True, "payload": payload, "error": ""}
    except Exception as exc:
        return {"ok": False, "payload": {}, "error": f"{name}更新失败：{exc}"}


def main() -> None:
    etf = safe_call("ETF", app.etf_monitor, True)
    stocks = safe_call("A股", app.scan_stocks, True, 50)
    payload = {
        "generated_at": dt.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
        "etf": etf,
        "stocks": stocks,
        "version": "mobile-cloud-1",
    }
    (DATA_DIR / "latest.json").write_text(
        json.dumps(app.clean_json(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {DATA_DIR / 'latest.json'}")


if __name__ == "__main__":
    main()
