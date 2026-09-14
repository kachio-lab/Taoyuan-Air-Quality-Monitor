"""歷史資料前處理（計畫書五、七-1）

將環境部原始「一列一測項、00~23 橫向排列」的寬表 CSV，
逆透視（melt）為「時間戳記 (timestamp) - 測項 (feature)」的長表時間序列結構，
並補值、衍生時間特徵，輸出整理後的乾淨資料集。

用法：
    python src/historical_preprocess.py
"""
import logging
import pandas as pd

from config import RAW_HISTORICAL_DIR, PROCESSED_DIR, HOURLY_COLUMNS, TAOYUAN_STATIONS
from imputation import add_time_features, nearest_neighbor_impute

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def melt_station_csv(csv_path) -> pd.DataFrame:
    """讀取單一測站的寬表 CSV，melt + pivot 為長表（每列一個時間戳記，各測項為欄位）"""
    raw = pd.read_csv(csv_path, encoding="utf-8-sig")

    # 去除全空的溢出欄位（如 Unnamed: 27）
    raw = raw.loc[:, ~raw.columns.str.startswith("Unnamed")]

    id_cols = ["測站", "日期", "測項"]
    hour_cols = [c for c in HOURLY_COLUMNS if c in raw.columns]

    melted = raw.melt(
        id_vars=id_cols,
        value_vars=hour_cols,
        var_name="hour",
        value_name="value",
    )

    # 組出完整時間戳記：日期 + 小時
    melted["date"] = pd.to_datetime(melted["日期"], errors="coerce").dt.date.astype(str)
    melted["timestamp"] = pd.to_datetime(
        melted["date"] + " " + melted["hour"] + ":00:00", errors="coerce"
    )
    melted["value"] = pd.to_numeric(melted["value"], errors="coerce")

    # pivot：每個測項變成一個欄位
    wide = melted.pivot_table(
        index=["測站", "timestamp"],
        columns="測項",
        values="value",
        aggfunc="first",
    ).reset_index()
    wide.columns.name = None

    return wide


def build_historical_dataset() -> pd.DataFrame:
    csv_files = sorted(RAW_HISTORICAL_DIR.glob("*.csv"))
    if not csv_files:
        raise FileNotFoundError(
            f"找不到任何歷史 CSV，請將 {TAOYUAN_STATIONS} 各站的原始寬表 CSV "
            f"放入 {RAW_HISTORICAL_DIR}"
        )

    frames = []
    for path in csv_files:
        logger.info("處理歷史檔案：%s", path.name)
        frames.append(melt_station_csv(path))

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(["測站", "timestamp"]).reset_index(drop=True)

    value_cols = [c for c in combined.columns if c not in ("測站", "timestamp")]
    combined = nearest_neighbor_impute(combined, value_cols)
    combined = add_time_features(combined)

    return combined


def main():
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    dataset = build_historical_dataset()

    out_path = PROCESSED_DIR / "historical_long.csv"
    dataset.to_csv(out_path, index=False, encoding="utf-8-sig")
    logger.info("完成，共 %d 列，輸出至 %s", len(dataset), out_path)


if __name__ == "__main__":
    main()
