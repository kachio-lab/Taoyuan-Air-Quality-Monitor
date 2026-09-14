"""把手上既有的歷史檔整併成一份標準長表 data/monitor/history.csv（訓練用）。

支援兩種來源，統一放在 data/raw_historical/：
1. 環境部下載的原始寬表（一列一測項、00~23 橫向排列），例如 `data/raw_historical/桃園_2025.csv`
2. 課堂整理過的長表（例如 data/raw_historical/2026_01_07桃園五站空品監測資料_精簡20欄合體版.csv）

只需要跑一次；之後資料由 pipeline/fetch.py 每小時累積。
用法：python -m pipeline.build_history
"""
from __future__ import annotations

import logging
import re

import pandas as pd

from pipeline.settings import (
    BASE_COLUMNS,
    HISTORY_CSV,
    MONITOR_DIR,
    RAW_HISTORICAL_DIR,
    SCHEMA_COLUMNS,
    STATIONS,
    WIDE_ITEM_MAP,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

HOUR_COLS = [f"{h:02d}" for h in range(24)]
# 環境部原始資料的品質註記：#（儀器維護）、*（程式檢核）、x（無效）、A（缺值）、ND、-
INVALID_TOKEN = re.compile(r"[#*xANDnd\-\s]+$")


def _clean_numeric(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip()
    s = s.str.replace(INVALID_TOKEN, "", regex=True)
    return pd.to_numeric(s, errors="coerce")


def load_wide_csv(path) -> pd.DataFrame:
    raw = pd.read_csv(path, encoding="utf-8-sig")
    raw = raw.loc[:, ~raw.columns.str.startswith("Unnamed")]
    hour_cols = [c for c in HOUR_COLS if c in raw.columns]

    melted = raw.melt(
        id_vars=["測站", "日期", "測項"],
        value_vars=hour_cols,
        var_name="hour",
        value_name="value",
    )
    date = pd.to_datetime(melted["日期"], errors="coerce").dt.normalize()
    melted["publishtime"] = date + pd.to_timedelta(melted["hour"].astype(int), unit="h")
    melted["value"] = _clean_numeric(melted["value"])
    melted["item"] = melted["測項"].map(WIDE_ITEM_MAP)
    melted = melted.dropna(subset=["item", "publishtime"])

    wide = melted.pivot_table(
        index=["測站", "publishtime"], columns="item", values="value", aggfunc="first"
    ).reset_index()
    wide.columns.name = None
    return wide.rename(columns={"測站": "sitename"})


def load_long_csv(path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig")
    df.columns = [c.strip().lower() for c in df.columns]
    if "sitename" not in df.columns or "publishtime" not in df.columns:
        raise ValueError(f"{path} 缺少 sitename / publishtime 欄位")
    df["publishtime"] = pd.to_datetime(df["publishtime"], errors="coerce")
    return df


def collect_sources() -> list[pd.DataFrame]:
    frames: list[pd.DataFrame] = []

    if not RAW_HISTORICAL_DIR.is_dir():
        return frames

    # 1) 原始寬表：data/raw_historical/ 直接底下的 <測站>_<年>.csv
    #    （其他非桃園地區的北部測站檔案若放在 其他北部測站_未使用/ 子資料夾，這裡的 station 檢查會自動略過）
    for path in sorted(RAW_HISTORICAL_DIR.glob("*.csv")):
        station = path.stem.split("_")[0]
        if station not in STATIONS:
            continue
        logger.info("讀取寬表：%s", path.name)
        frames.append(load_wide_csv(path))

    # 2) 已整理好的長表（放在 長表格式/ 子資料夾，和寬表分開，
    #    避免 src/historical_preprocess.py 的單層 glob 誤把長表當寬表處理）
    long_dir = RAW_HISTORICAL_DIR / "長表格式"
    for path in sorted(long_dir.glob("*桃園五站空品監測資料*.csv")):
        logger.info("讀取長表：%s", path.name)
        frames.append(load_long_csv(path))
    for path in sorted(long_dir.glob("taoyuan_air_qulity*.csv")):
        logger.info("讀取長表：%s", path.name)
        frames.append(load_long_csv(path))

    return frames


def main() -> None:
    frames = collect_sources()
    if not frames:
        raise SystemExit("找不到任何歷史資料來源")

    combined = pd.concat(frames, ignore_index=True)
    for col in SCHEMA_COLUMNS:
        if col not in combined.columns:
            combined[col] = pd.NA
    combined = combined[SCHEMA_COLUMNS]

    combined = combined[combined["sitename"].isin(STATIONS)]
    combined["publishtime"] = pd.to_datetime(combined["publishtime"], errors="coerce")
    combined = combined.dropna(subset=["publishtime"])
    combined["publishtime"] = combined["publishtime"].dt.floor("h")
    for col in BASE_COLUMNS:
        combined[col] = pd.to_numeric(combined[col], errors="coerce")

    # 同測站同時間有多筆時，保留非空欄位最多的那筆
    combined["_filled"] = combined[BASE_COLUMNS].notna().sum(axis=1)
    combined = (
        combined.sort_values(["sitename", "publishtime", "_filled"])
        .drop_duplicates(subset=["sitename", "publishtime"], keep="last")
        .drop(columns=["_filled"])
        .sort_values(["sitename", "publishtime"])
        .reset_index(drop=True)
    )

    MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    combined.to_csv(HISTORY_CSV, index=False, encoding="utf-8-sig")

    logger.info("輸出 %s：%d 列", HISTORY_CSV, len(combined))
    for station, grp in combined.groupby("sitename"):
        logger.info(
            "  %s：%d 列（%s ~ %s），pm2.5 缺值率 %.1f%%",
            station, len(grp), grp["publishtime"].min(), grp["publishtime"].max(),
            grp["pm2.5"].isna().mean() * 100,
        )


if __name__ == "__main__":
    main()
