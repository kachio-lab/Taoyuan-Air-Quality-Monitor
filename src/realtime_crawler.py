"""即時空氣品質資料爬蟲（計畫書第八章）

每小時向環境部開放 API（aqx_p_432）拉取一次桃園地區 5 個測站的即時觀測資料，
篩選、整理成與歷史資料相同的欄位結構，並累加寫入整理後的資料集。

僅做到「資料整理」階段：抓取 -> 篩選 -> 欄位對照 -> 缺值檢查 -> 時間特徵衍生 -> 落地存檔。
不含正規化（MinMax）、模型推論等建模步驟。

用法：
    python src/realtime_crawler.py            # 執行一次
    python src/realtime_crawler.py --loop      # 常駐執行，每小時 15 分自動拉取一次
"""
import argparse
import json
import logging
import time
from datetime import datetime

import pandas as pd
import requests

from config import (
    MOENV_API_URL,
    MOENV_API_KEY,
    TAOYUAN_STATIONS,
    API_FIELD_MAP,
    REALTIME_RAW_DIR,
    PROCESSED_DIR,
)
from imputation import add_time_features
import weather_crawler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15
MAX_RETRIES = 3


def fetch_raw_records() -> list[dict]:
    """呼叫環境部開放 API，回傳原始 JSON records（未篩選測站）"""
    if not MOENV_API_KEY:
        raise RuntimeError(
            "缺少 MOENV_API_KEY，請於 .env 檔設定（申請網址：https://data.moenv.gov.tw/）"
        )

    params = {"api_key": MOENV_API_KEY, "format": "JSON"}

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(MOENV_API_URL, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
            # API 直接回傳 list；若未來改版包成 {"records": [...]} 一併相容
            return payload if isinstance(payload, list) else payload.get("records", [])
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            logger.warning("第 %d 次呼叫 API 失敗：%s", attempt, exc)
            time.sleep(2 * attempt)

    raise RuntimeError(f"呼叫環境部 API 連續失敗 {MAX_RETRIES} 次：{last_error}")


def save_raw_snapshot(records: list[dict]) -> None:
    """原始 JSON 落地存檔，供稽核與問題回溯"""
    REALTIME_RAW_DIR.mkdir(parents=True, exist_ok=True)
    fname = REALTIME_RAW_DIR / f"aqx_p_432_{datetime.now():%Y%m%d_%H%M%S}.json"
    fname.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("原始資料存檔：%s", fname)


def filter_and_map(records: list[dict]) -> pd.DataFrame:
    """篩選桃園 5 站資料，並以 API_FIELD_MAP 對照為專案標準欄位"""
    df = pd.DataFrame(records)
    if df.empty:
        logger.warning("API 回傳空資料")
        return df

    df = df[df["sitename"].isin(TAOYUAN_STATIONS)].copy()
    if df.empty:
        logger.warning("此次拉取無桃園地區測站資料")
        return df

    df = df.rename(columns=API_FIELD_MAP)
    keep_cols = ["測站", "日期"] + [v for v in API_FIELD_MAP.values() if v not in ("測站", "日期")]
    keep_cols = [c for c in keep_cols if c in df.columns]
    df = df[keep_cols]

    for col in df.columns:
        if col not in ("測站", "日期"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["timestamp"] = pd.to_datetime(df["日期"], errors="coerce")
    df = df.drop(columns=["日期"])

    return df


def enrich_with_weather(df: pd.DataFrame) -> pd.DataFrame:
    """補上 aqx_p_432 缺少的氣象欄位（AMB_TEMP/RH/RAINFALL），來源：中央氣象署 CWA API。

    CWA 呼叫失敗不應中斷整條爬蟲（AQI 資料仍然有效），因此僅記錄警告並保留空值欄位。
    """
    for col in ("AMB_TEMP", "RH", "RAINFALL"):
        if col not in df.columns:
            df[col] = pd.NA

    if df.empty:
        return df

    try:
        weather_df = weather_crawler.get_weather_features(df)
    except Exception as exc:  # noqa: BLE001 - 氣象資料為輔助欄位，失敗不應中斷主流程
        logger.warning("取得氣象資料失敗，AMB_TEMP/RH/RAINFALL 將維持缺值：%s", exc)
        return df

    if weather_df.empty:
        return df

    df = df.drop(columns=["AMB_TEMP", "RH", "RAINFALL"]).merge(weather_df, on="測站", how="left")
    return df


def append_to_processed(df: pd.DataFrame) -> None:
    """整理後資料累加寫入 processed 資料集（去重：同測站同時間戳記只保留最新一筆）"""
    if df.empty:
        return

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED_DIR / "realtime_long.csv"

    df = add_time_features(df)

    if out_path.exists():
        existing = pd.read_csv(out_path, encoding="utf-8-sig", parse_dates=["timestamp"])
        combined = pd.concat([existing, df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["測站", "timestamp"], keep="last")
    else:
        combined = df

    combined = combined.sort_values(["測站", "timestamp"]).reset_index(drop=True)
    combined.to_csv(out_path, index=False, encoding="utf-8-sig")
    logger.info("已累加寫入 %s（累計 %d 列，本次新增/更新 %d 列）", out_path, len(combined), len(df))


def run_once() -> None:
    logger.info("開始拉取即時空氣品質資料...")
    records = fetch_raw_records()
    save_raw_snapshot(records)
    df = filter_and_map(records)
    df = enrich_with_weather(df)
    append_to_processed(df)
    logger.info("本次拉取完成，桃園地區有效筆數：%d", len(df))


def run_loop() -> None:
    """常駐模式：每小時 15 分觸發（環境部通常於此時完成前一小時資料上架，計畫書八-2）"""
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler(timezone="Asia/Taipei")
    scheduler.add_job(run_once, "cron", minute=15)
    logger.info("排程已啟動，每小時 15 分自動拉取一次（Ctrl+C 結束）")

    run_once()  # 啟動時先執行一次
    scheduler.start()


def main():
    parser = argparse.ArgumentParser(description="桃園地區即時空氣品質資料爬蟲")
    parser.add_argument("--loop", action="store_true", help="常駐執行，每小時自動拉取")
    args = parser.parse_args()

    if args.loop:
        run_loop()
    else:
        run_once()


if __name__ == "__main__":
    main()
