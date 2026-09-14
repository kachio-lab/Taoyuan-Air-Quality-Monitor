"""遺失值補值與時間特徵衍生（計畫書七-1：資料預處理與特徵工程）"""
import pandas as pd


def add_time_features(df: pd.DataFrame, timestamp_col: str = "timestamp") -> pd.DataFrame:
    """由時間戳記衍生 年積日(day_of_year) 與 日積時(hour_of_day)"""
    ts = pd.to_datetime(df[timestamp_col])
    df = df.copy()
    df["day_of_year"] = ts.dt.dayofyear
    df["hour_of_day"] = ts.dt.hour
    return df


def nearest_neighbor_impute(df: pd.DataFrame, value_cols: list[str], group_col: str = "測站") -> pd.DataFrame:
    """最鄰近值填補法：依測站分組，沿時間序列以前後最近的有效值補值。

    文獻與計畫書要求時間序列不可中斷，故採 ffill 再 bfill（等同取最鄰近的前一筆／後一筆有效觀測）。
    """
    df = df.sort_values([group_col, "timestamp"]).copy()
    df[value_cols] = (
        df.groupby(group_col)[value_cols]
        .apply(lambda g: g.ffill().bfill())
        .reset_index(level=0, drop=True)
    )
    return df
