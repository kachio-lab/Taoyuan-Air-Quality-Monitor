"""用最新觀測產生未來 1 / 3 小時的 PM2.5 與 OX 預測，累加寫入 data/monitor/predictions.csv。

每一列同時記下 persistence 基準值，方便後續評分。

用法：python -m pipeline.predict
"""
from __future__ import annotations

import logging
from datetime import datetime

import joblib
import numpy as np
import pandas as pd

from pipeline.features import build_features
from pipeline.settings import (
    HISTORY_CSV,
    HORIZONS,
    MODEL_DIR,
    MODEL_META_JSON,
    MONITOR_DIR,
    OBSERVATIONS_CSV,
    PREDICTIONS_CSV,
    TARGET_COLUMNS,
    TARGET_STATION,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MIN_HISTORY_HOURS = 25

PREDICTION_COLUMNS = [
    "run_at", "station", "base_time", "horizon_h", "target_time",
    "pm25_pred", "pm25_persistence",
    "ox_pred",  "ox_persistence",
    "algorithm_pm25", "algorithm_ox",
    "warmup",
]


def load_recent_observations(station: str, hours: int = 72) -> pd.DataFrame:
    frames = []
    if OBSERVATIONS_CSV.exists():
        frames.append(pd.read_csv(OBSERVATIONS_CSV, encoding="utf-8-sig", parse_dates=["publishtime"]))
    if not frames:
        raise SystemExit("尚無即時觀測資料，請先執行 python -m pipeline.fetch")

    obs = pd.concat(frames, ignore_index=True)
    obs = obs[obs["sitename"] == station].copy()
    if obs.empty:
        raise SystemExit(f"觀測資料中沒有 {station} 測站")

    latest = obs["publishtime"].max()
    window_start = latest - pd.Timedelta(hours=hours)
    obs = obs[obs["publishtime"] >= window_start]

    if len(obs) < MIN_HISTORY_HOURS and HISTORY_CSV.exists():
        hist = pd.read_csv(HISTORY_CSV, encoding="utf-8-sig", parse_dates=["publishtime"])
        hist = hist[(hist["sitename"] == station) & (hist["publishtime"] >= window_start)]
        if not hist.empty:
            obs = pd.concat([hist, obs], ignore_index=True)

    return (
        obs.drop_duplicates(subset=["publishtime"], keep="last")
        .sort_values("publishtime")
        .reset_index(drop=True)
    )


def predict_target(
    feats: pd.DataFrame,
    latest: pd.Series,
    station: str,
    horizon: int,
    target: str,
    persistence_override: float | None = None,
) -> tuple[float | None, float | None, str | None]:
    """對單一目標、單一時距做推論。回傳 (y_pred, persistence, algorithm)。"""
    safe_target = target.replace(".", "")
    model_path = MODEL_DIR / f"{safe_target}_{station}_h{horizon}.joblib"
    if not model_path.exists():
        logger.warning(
            "找不到模型 %s，略過（請先執行 python -m pipeline.train --target %s）",
            model_path.name, target,
        )
        return None, None, None

    bundle = joblib.load(model_path)
    model, feature_cols = bundle["model"], bundle["feature_cols"]

    missing = [c for c in feature_cols if c not in feats.columns]
    for col in missing:
        feats[col] = np.nan
    if missing:
        logger.warning("特徵 %s 不存在於即時資料，以缺值處理", missing)

    X = latest.reindex(feature_cols).to_numpy(dtype=float).reshape(1, -1)
    y_pred = float(model.predict(X)[0])
    y_pred = max(y_pred, 0.0)

    now_col = f"{target.replace('.', '')}_now" if target != "pm2.5" else "pm2.5_now"
    # ox_now / pm2.5_now 都已在 build_features 裡建立
    if persistence_override is not None:
        persistence = persistence_override
    else:
        persistence_col = "ox_now" if target == "ox" else "pm2.5_now"
        persistence = float(latest.get(persistence_col, np.nan))
    algorithm = type(model.named_steps["model"]).__name__
    return round(y_pred, 2), round(persistence, 2) if not np.isnan(persistence) else None, algorithm


def append_predictions(rows: list[dict]) -> pd.DataFrame:
    new = pd.DataFrame(rows, columns=PREDICTION_COLUMNS)
    MONITOR_DIR.mkdir(parents=True, exist_ok=True)

    if PREDICTIONS_CSV.exists():
        old = pd.read_csv(
            PREDICTIONS_CSV, encoding="utf-8-sig",
            parse_dates=["run_at", "base_time", "target_time"],
        )
        combined = pd.concat([old, new], ignore_index=True)
    else:
        combined = new

    combined = (
        combined.drop_duplicates(subset=["station", "base_time", "horizon_h"], keep="last")
        .sort_values(["station", "target_time", "horizon_h"])
        .reset_index(drop=True)
    )
    combined.to_csv(PREDICTIONS_CSV, index=False, encoding="utf-8-sig")
    logger.info("預測累計 %d 列 -> %s", len(combined), PREDICTIONS_CSV)
    return combined


def main(station: str = TARGET_STATION) -> pd.DataFrame:
    obs = load_recent_observations(station)
    feats = build_features(obs)

    valid = feats[feats["pm2.5_now"].notna()]
    if valid.empty:
        raise SystemExit("最新觀測的 pm2.5 全為缺值，無法預測")
    latest = valid.iloc[-1]
    base_time = latest["publishtime"]
    # 直接從obs算最新OX實測值當persistence基準
    obs_ox = feats[feats["ox"].notna()]
    ox_persistence_val = float(obs_ox["ox"].iloc[-1]) if not obs_ox.empty else None
    history_hours = int(feats["pm2.5"].notna().sum())
    warmup = history_hours < MIN_HISTORY_HOURS
    if warmup:
        logger.warning(
            "暖機中：目前只有 %d 小時觀測（需 %d 小時），預測僅供參考",
            history_hours, MIN_HISTORY_HOURS,
        )

    age_hours = (pd.Timestamp.now().floor("h") - base_time) / pd.Timedelta(hours=1)
    if age_hours > 3:
        logger.warning("最新觀測時間 %s 已落後 %.0f 小時，資料源可能延遲", base_time, age_hours)

    run_at = datetime.now().replace(microsecond=0)
    rows = []

    for horizon in HORIZONS:
        pm25_pred, pm25_pers, algo_pm25 = predict_target(feats, latest, station, horizon, "pm2.5")
        ox_pred,   ox_pers,   algo_ox   = predict_target(feats, latest, station, horizon, "ox", persistence_override=ox_persistence_val)

        if pm25_pred is None and ox_pred is None:
            continue   # 兩個模型都找不到，跳過

        rows.append({
            "run_at":          run_at,
            "station":         station,
            "base_time":       base_time,
            "horizon_h":       horizon,
            "target_time":     base_time + pd.Timedelta(hours=horizon),
            "pm25_pred":       pm25_pred,
            "pm25_persistence":pm25_pers,
            "ox_pred":         ox_pred,
            "ox_persistence":  ox_pers,
            "algorithm_pm25":  algo_pm25,
            "algorithm_ox":    algo_ox,
            "warmup":          warmup,
        })

        if pm25_pred is not None:
            logger.info(
                "基準 %s -> +%dh  PM2.5=%.1f µg/m³ OX=%.1f ppb",
                base_time, horizon,
                pm25_pred, ox_pred if ox_pred is not None else float("nan"),
            )

    if not rows:
        raise SystemExit("沒有任何可用模型，無法產生預測")
    return append_predictions(rows)


if __name__ == "__main__":
    main()
