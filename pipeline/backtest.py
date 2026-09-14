"""歷史回測：用「訓練期／驗證期」時間切分，給出上線第一天就看得到的成效基準。

即時監控的成效統計要累積幾天才有意義，回測則是立刻可得的參考值：
拿歷史資料的最後 N 天當作沒看過的未來，看模型在那段期間的實際表現。

輸出 docs/backtest.json（儀表板會一起顯示）。
用法：python -m pipeline.backtest --holdout-days 45
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime

import numpy as np
import pandas as pd

from pipeline.features import make_supervised
from pipeline.settings import DOCS_DIR, HORIZONS, MODEL_META_JSON, TARGET_STATION
from pipeline.train import candidate_models, load_training_frame

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BACKTEST_JSON = DOCS_DIR / "backtest.json"
DEFAULT_HOLDOUT_DAYS = 45


def run(station: str, holdout_days: int) -> dict:
    df = load_training_frame(station)
    meta = json.loads(MODEL_META_JSON.read_text(encoding="utf-8")) if MODEL_META_JSON.exists() else {"models": {}}

    result = {
        "station": station,
        "holdout_days": holdout_days,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "horizons": {},
    }

    for horizon in HORIZONS:
        X_df, y, cols = make_supervised(df, horizon)
        cutoff = X_df["publishtime"].max() - pd.Timedelta(days=holdout_days)
        train_mask = X_df["publishtime"] < cutoff
        test_mask = ~train_mask
        if test_mask.sum() < 24:
            logger.warning("h=%d 驗證期樣本不足，略過", horizon)
            continue

        algo = meta.get("models", {}).get(str(horizon), {}).get("best_algorithm", "RandomForest")
        model = candidate_models()[algo]

        X = X_df[cols].to_numpy(dtype=float)
        y_arr = y.to_numpy(dtype=float)
        model.fit(X[train_mask.to_numpy()], y_arr[train_mask.to_numpy()])
        pred = np.clip(model.predict(X[test_mask.to_numpy()]), 0, None)

        truth = y_arr[test_mask.to_numpy()]
        base = X_df.loc[test_mask, "persistence"].to_numpy(dtype=float)
        ok = ~np.isnan(base)

        err = pred - truth
        mae = float(np.mean(np.abs(err)))
        base_mae = float(np.mean(np.abs(base[ok] - truth[ok])))
        ss_tot = float(np.sum((truth - truth.mean()) ** 2))

        result["horizons"][str(horizon)] = {
            "algorithm": algo,
            "train_rows": int(train_mask.sum()),
            "test_rows": int(test_mask.sum()),
            "test_start": str(X_df.loc[test_mask, "publishtime"].min()),
            "test_end": str(X_df.loc[test_mask, "publishtime"].max()),
            "mae": round(mae, 3),
            "rmse": round(float(np.sqrt(np.mean(err ** 2))), 3),
            "bias": round(float(np.mean(err)), 3),
            "r2": round(1 - float(np.sum(err ** 2)) / ss_tot, 3) if ss_tot > 0 else None,
            "baseline_mae": round(base_mae, 3),
            "skill_vs_baseline_pct": round((base_mae - mae) / base_mae * 100, 1) if base_mae > 0 else None,
            "coverage_pct": round(float(np.mean(np.abs(err) <= 5)) * 100, 1),
        }
        r = result["horizons"][str(horizon)]
        logger.info(
            "h=%d（%s ~ %s，%d 筆）MAE=%.2f RMSE=%.2f R²=%s，勝過 persistence %.1f%%",
            horizon, r["test_start"], r["test_end"], r["test_rows"],
            r["mae"], r["rmse"], r["r2"], r["skill_vs_baseline_pct"],
        )

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    BACKTEST_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("回測結果 -> %s", BACKTEST_JSON)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="PM2.5 模型歷史回測")
    parser.add_argument("--station", default=TARGET_STATION)
    parser.add_argument("--holdout-days", type=int, default=DEFAULT_HOLDOUT_DAYS)
    args = parser.parse_args()
    run(args.station, args.holdout_days)


if __name__ == "__main__":
    main()
