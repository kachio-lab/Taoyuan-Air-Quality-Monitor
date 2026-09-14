"""特徵工程：訓練與即時預測共用同一組函式。

沿用建模 notebook 的設計（時間循環編碼、風向分解、滯後特徵、科學指標），
但補上兩件上線必須的事：
1. 先把時間軸補成「每小時一列」的完整格點，滯後特徵才真的是「N 小時前」
   （原本 shift(1) 在缺資料時會偷偷取到更早的時間）。
2. 特徵欄位清單固定寫死並存進模型 metadata，避免訓練與推論欄位順序不一致。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.settings import BASE_COLUMNS

LAGS = [1, 2, 3, 6, 12, 24]
LAG_SOURCES = ["pm2.5", "ox"]
ROLL_WINDOWS = [3, 24]


def to_hourly_grid(df: pd.DataFrame) -> pd.DataFrame:
    """單一測站：依 publishtime 排序，補齊缺漏的整點成為連續時間軸。"""
    df = df.copy()
    df["publishtime"] = pd.to_datetime(df["publishtime"], errors="coerce")
    df = df.dropna(subset=["publishtime"]).sort_values("publishtime")
    df = df.drop_duplicates(subset=["publishtime"], keep="last")

    df["publishtime"] = df["publishtime"].dt.floor("h")
    df = df.drop_duplicates(subset=["publishtime"], keep="last")

    full_index = pd.date_range(
        df["publishtime"].min(), df["publishtime"].max(), freq="h"
    )
    df = df.set_index("publishtime").reindex(full_index)
    df.index.name = "publishtime"
    return df.reset_index()


def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    """科學指標與時間/風向編碼。"""
    df = df.copy()
    for col in BASE_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # 總氧化劑
    df["ox"] = df["o3"] + df["no2"]
    # 粗細懸浮微粒比值
    df["pm_ratio"] = np.where(df["pm10"] > 0, df["pm2.5"] / df["pm10"], np.nan)
    # 特徵氣體比值
    df["co_nox_ratio"] = np.where(df["nox"] > 0, df["co"] / df["nox"], np.nan)
    df["so2_nox_ratio"] = np.where(df["nox"] > 0, df["so2"] / df["nox"], np.nan)
    # 水蒸氣壓
    es = 6.112 * np.exp((17.67 * df["amb_temp"]) / (df["amb_temp"] + 243.5))
    df["sat_vapor_press"] = es
    df["vapor_press"] = es * (df["rh"] / 100.0)

    # 風向向量分量
    rad = np.radians(df["wind_direc"])
    df["wind_x"] = df["wind_speed"] * np.cos(rad)
    df["wind_y"] = df["wind_speed"] * np.sin(rad)

    ts = df["publishtime"]
    hour, month = ts.dt.hour, ts.dt.month
    df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    df["month_sin"] = np.sin(2 * np.pi * month / 12)
    df["month_cos"] = np.cos(2 * np.pi * month / 12)
    df["day_of_year"] = ts.dt.dayofyear
    return df


def add_lags(df: pd.DataFrame) -> pd.DataFrame:
    """滯後與滾動特徵。前提：df 已經是連續每小時格點。"""
    df = df.copy()
    for col in LAG_SOURCES:
        for lag in LAGS:
            df[f"{col}_lag{lag}"] = df[col].shift(lag)
    for win in ROLL_WINDOWS:
        df[f"pm2.5_roll{win}_mean"] = df["pm2.5"].rolling(win, min_periods=1).mean()
        df[f"pm2.5_roll{win}_std"] = df["pm2.5"].rolling(win, min_periods=2).std()
    df["pm2.5_diff1"] = df["pm2.5"].diff(1)
    df["pm2.5_diff3"] = df["pm2.5"].diff(3)
    return df


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """單一測站的長表 -> 特徵表（含 publishtime 與原始值）。"""
    out = to_hourly_grid(df)
    out = add_derived(out)
    out = add_lags(out)
    out["pm2.5_now"] = out["pm2.5"]
    out["ox_now"] = out["ox"]
    return out


def feature_columns(df: pd.DataFrame, target: str = "pm2.5") -> list[str]:
    """特徵欄位＝除了識別欄與目標欄以外的所有數值欄。"""
    exclude = {
        "publishtime", "sitename", "pm2.5", "ox", "longitude", "latitude",
        "persistence", "y",
    }
    cols = [
        c for c in df.columns
        if c not in exclude and pd.api.types.is_numeric_dtype(df[c])
    ]
    return sorted(cols)


def make_supervised(
    df: pd.DataFrame,
    horizon: int,
    target: str = "pm2.5",
) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    """建立監督式學習矩陣：以 t 時刻的特徵預測 t+horizon 的 target。

    target: 'pm2.5' 或 'ox'
    """
    if target not in ("pm2.5", "ox"):
        raise ValueError(f"不支援的目標變數：{target}，請用 'pm2.5' 或 'ox'")

    feats = build_features(df)
    feats["y"] = feats[target].shift(-horizon)
    feats["pm2.5_now"] = feats["pm2.5"]
    feats["ox_now"] = feats["ox"]
    feats["persistence"] = feats[target]   # 同目標的 persistence 基準

    cols = feature_columns(feats, target)
    usable = feats.dropna(subset=["y"]).copy()
    usable = usable.dropna(subset=["pm2.5_lag1"])   # 至少要有 lag1
    return usable[["publishtime", "persistence"] + cols], usable["y"], cols