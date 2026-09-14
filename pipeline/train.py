"""訓練 PM2.5 與 OX 預報模型（每個目標、每個預測時距各一個模型）。

流程：載入 history + 上線後累積的 observations -> 特徵工程 ->
TimeSeriesSplit 交叉驗證比較候選演算法 -> 取 CV RMSE 最低者用全部資料重訓 -> 存檔。

模型檔命名：{target}_{station}_h{horizon}.joblib
  例：pm2.5_桃園_h1.joblib、ox_桃園_h3.joblib

用法：python -m pipeline.train                        # 訓練所有目標 × HORIZONS
      python -m pipeline.train --target ox             # 只訓練 OX
      python -m pipeline.train --target pm2.5 --horizon 1
"""
from __future__ import annotations

import argparse
import json
import logging
import warnings
from datetime import datetime

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import MinMaxScaler

from pipeline.features import make_supervised
from pipeline.settings import (
    HISTORY_CSV,
    HORIZONS,
    MODEL_DIR,
    MODEL_META_JSON,
    OBSERVATIONS_CSV,
    TARGET_COLUMNS,
    TARGET_STATION,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

warnings.filterwarnings("ignore", message="X does not have valid feature names")

CV_SPLITS = 5
CV_GAP = 24
SELECTION_TOLERANCE = 0.01
PREFERENCE_ORDER = ["HistGradientBoosting", "LightGBM", "Ridge", "RandomForest"]


def candidate_models() -> dict[str, Pipeline]:
    def wrap(model):
        return Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("scale", MinMaxScaler()),
            ("model", model),
        ])

    models = {
        "HistGradientBoosting": wrap(
            HistGradientBoostingRegressor(
                max_iter=400, learning_rate=0.06, max_depth=None,
                min_samples_leaf=20, l2_regularization=1.0, random_state=42,
            )
        ),
        "RandomForest": wrap(
            RandomForestRegressor(
                n_estimators=120, min_samples_leaf=20, n_jobs=-1, random_state=42,
            )
        ),
        "Ridge": wrap(Ridge(alpha=1.0)),
    }
    try:
        from lightgbm import LGBMRegressor
        models["LightGBM"] = wrap(
            LGBMRegressor(n_estimators=500, learning_rate=0.05, random_state=42, verbose=-1)
        )
    except ImportError:
        logger.info("未安裝 lightgbm，略過該候選演算法")
    return models


def load_training_frame(station: str) -> pd.DataFrame:
    frames = []
    if HISTORY_CSV.exists():
        frames.append(pd.read_csv(HISTORY_CSV, encoding="utf-8-sig", parse_dates=["publishtime"]))
    if OBSERVATIONS_CSV.exists():
        frames.append(pd.read_csv(OBSERVATIONS_CSV, encoding="utf-8-sig", parse_dates=["publishtime"]))
    if not frames:
        raise SystemExit("沒有訓練資料，請先執行 python -m pipeline.build_history")

    df = pd.concat(frames, ignore_index=True)
    df = df[df["sitename"] == station].copy()
    if df.empty:
        raise SystemExit(f"找不到測站 {station} 的資料")
    return df.sort_values("publishtime")


def cross_validate(model: Pipeline, X: np.ndarray, y: np.ndarray) -> dict:
    tscv = TimeSeriesSplit(n_splits=CV_SPLITS, gap=CV_GAP)
    rmses, maes, r2s = [], [], []
    for train_idx, val_idx in tscv.split(X):
        model.fit(X[train_idx], y[train_idx])
        pred = model.predict(X[val_idx])
        rmses.append(float(np.sqrt(mean_squared_error(y[val_idx], pred))))
        maes.append(float(mean_absolute_error(y[val_idx], pred)))
        r2s.append(float(r2_score(y[val_idx], pred)))
    return {
        "cv_rmse": float(np.mean(rmses)),
        "cv_mae": float(np.mean(maes)),
        "cv_r2": float(np.mean(r2s)),
        "cv_rmse_folds": [round(v, 4) for v in rmses],
    }


def select_best(results: dict) -> str:
    lowest = min(r["cv_rmse"] for r in results.values())
    tied = [k for k, r in results.items() if r["cv_rmse"] <= lowest * (1 + SELECTION_TOLERANCE)]
    for name in PREFERENCE_ORDER:
        if name in tied:
            return name
    return min(tied, key=lambda k: results[k]["cv_rmse"])


def train_one(station: str, horizon: int, target: str) -> dict:
    df = load_training_frame(station)
    X_df, y, feature_cols = make_supervised(df, horizon, target=target)
    X = X_df[feature_cols].to_numpy(dtype=float)
    y_arr = y.to_numpy(dtype=float)

    logger.info(
        "[%s h=%d] 訓練樣本 %d 筆、特徵 %d 個（%s ~ %s）",
        target, horizon, len(X), len(feature_cols),
        X_df["publishtime"].min(), X_df["publishtime"].max(),
    )

    baseline_pred = X_df["persistence"].to_numpy(dtype=float)
    mask = ~np.isnan(baseline_pred)
    baseline = {
        "rmse": float(np.sqrt(mean_squared_error(y_arr[mask], baseline_pred[mask]))),
        "mae": float(mean_absolute_error(y_arr[mask], baseline_pred[mask])),
    }

    results = {}
    for name, model in candidate_models().items():
        scores = cross_validate(model, X, y_arr)
        results[name] = scores
        logger.info(
            "  %-22s CV RMSE=%.3f MAE=%.3f R²=%.3f",
            name, scores["cv_rmse"], scores["cv_mae"], scores["cv_r2"],
        )

    best_name = select_best(results)
    best_model = candidate_models()[best_name]
    best_model.fit(X, y_arr)

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    # 命名規則：{target}_{station}_h{horizon}.joblib
    safe_target = target.replace(".", "")   # pm2.5 -> pm25
    model_path = MODEL_DIR / f"{safe_target}_{station}_h{horizon}.joblib"
    joblib.dump({"model": best_model, "feature_cols": feature_cols}, model_path, compress=3)

    logger.info(
        "[%s h=%d] 最佳演算法：%s（CV RMSE %.3f，persistence 基準 %.3f）-> %s",
        target, horizon, best_name, results[best_name]["cv_rmse"], baseline["rmse"], model_path.name,
    )

    return {
        "station": station,
        "target": target,
        "horizon": horizon,
        "model_file": model_path.name,
        "best_algorithm": best_name,
        "feature_count": len(feature_cols),
        "train_rows": int(len(X)),
        "train_start": str(X_df["publishtime"].min()),
        "train_end": str(X_df["publishtime"].max()),
        "cv": results[best_name],
        "cv_all": {k: round(v["cv_rmse"], 4) for k, v in results.items()},
        "persistence_baseline": baseline,
        "trained_at": datetime.now().isoformat(timespec="seconds"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="訓練 PM2.5 / OX 預報模型")
    parser.add_argument("--station", default=TARGET_STATION)
    parser.add_argument(
        "--target", action="append",
        help="可重複指定（--target pm2.5 --target ox），預設為設定檔的 TARGET_COLUMNS",
    )
    parser.add_argument("--horizon", type=int, action="append",
                        help="可重複指定，預設為設定檔的 HORIZONS")
    args = parser.parse_args()

    targets = args.target or TARGET_COLUMNS
    horizons = args.horizon or HORIZONS

    meta: dict = {"station": args.station, "models": {}}
    for tgt in targets:
        for h in horizons:
            key = f"{tgt}_h{h}"
            meta["models"][key] = train_one(args.station, h, tgt)

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_META_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("模型 metadata 已寫入 %s", MODEL_META_JSON)


if __name__ == "__main__":
    main()