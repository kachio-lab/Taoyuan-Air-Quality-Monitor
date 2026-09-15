"""把預測對上後來的實際觀測，算出「即時監控成效」。

輸出兩份東西：
- data/monitor/scores.csv：每一筆預測配對實際值後的逐筆誤差（可自行拉進 Excel 分析）
- docs/metrics.json：儀表板要用的彙總指標（近 24 小時 / 7 天 / 30 天 / 全部）

成效不是只看 MAE 好不好看，而是看有沒有贏過 persistence 基準線
（＝「假設下一小時濃度和現在一樣」），這是空品預報最誠實的比較對象。

用法：python -m pipeline.evaluate
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

import numpy as np
import pandas as pd

from pipeline.settings import (
    DOCS_DIR,
    METRICS_JSON,
    MODEL_META_JSON,
    MONITOR_DIR,
    OBSERVATIONS_CSV,
    PREDICTIONS_CSV,
    SCORES_CSV,
    TARGET_STATION,
    aqi_band,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

WINDOWS = {"24h": 1, "7d": 7, "30d": 30, "all": None}


def build_scores(station: str) -> pd.DataFrame:
    if not PREDICTIONS_CSV.exists():
        return pd.DataFrame()

    preds = pd.read_csv(
        PREDICTIONS_CSV, encoding="utf-8-sig",
        parse_dates=["run_at", "base_time", "target_time"],
    )
    preds = preds[preds["station"] == station]

    obs = pd.read_csv(OBSERVATIONS_CSV, encoding="utf-8-sig", parse_dates=["publishtime"])
    obs_station = obs[obs["sitename"] == station].copy()

    # PM2.5 實際值
    obs_pm25 = obs_station[["publishtime", "pm2.5"]].rename(
        columns={"publishtime": "target_time", "pm2.5": "y_true_pm25"}
    ).dropna(subset=["y_true_pm25"]).drop_duplicates("target_time", keep="last")

    # OX 實際值（o3 + no2）
    obs_ox = obs_station[["publishtime", "o3", "no2"]].copy()
    obs_ox["y_true_ox"] = pd.to_numeric(obs_ox["o3"], errors="coerce") + pd.to_numeric(obs_ox["no2"], errors="coerce")
    obs_ox = obs_ox[["publishtime", "y_true_ox"]].rename(
        columns={"publishtime": "target_time"}
    ).dropna(subset=["y_true_ox"]).drop_duplicates("target_time", keep="last")

    scored = preds.merge(obs_pm25, on="target_time", how="left")
    scored = scored.merge(obs_ox, on="target_time", how="left")

    # PM2.5 誤差
    scored["error"] = scored["pm25_pred"] - scored["y_true_pm25"]
    scored["abs_error"] = scored["error"].abs()
    scored["baseline_error"] = scored["pm25_persistence"] - scored["y_true_pm25"]
    scored["baseline_abs_error"] = scored["baseline_error"].abs()
    scored["pred_band"] = scored["pm25_pred"].map(lambda v: aqi_band(v)[0])
    scored["true_band"] = scored["y_true_pm25"].map(lambda v: aqi_band(v)[0])
    scored["band_hit"] = np.where(
        scored["y_true_pm25"].notna(), scored["pred_band"] == scored["true_band"], np.nan
    )

    # OX 誤差
    scored["ox_error"] = scored["ox_pred"] - scored["y_true_ox"]
    scored["ox_abs_error"] = scored["ox_error"].abs()
    scored["ox_baseline_error"] = scored["ox_persistence"] - scored["y_true_ox"]
    scored["ox_baseline_abs_error"] = scored["ox_baseline_error"].abs()

    # 向下相容：保留 y_true 欄位名稱給 summarise 用
    scored["y_true"] = scored["y_true_pm25"]

    MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    scored.sort_values(["target_time", "horizon_h"]).to_csv(
        SCORES_CSV, index=False, encoding="utf-8-sig"
    )
    return scored


def _metrics(df: pd.DataFrame) -> dict | None:
    df = df.dropna(subset=["y_true"])
    if df.empty:
        return None

    err = df["error"].to_numpy(dtype=float)
    base_err = df["baseline_error"].to_numpy(dtype=float)
    y_true = df["y_true"].to_numpy(dtype=float)

    mae = float(np.mean(np.abs(err)))
    base_mae = float(np.mean(np.abs(base_err)))
    denom = float(np.sum((y_true - y_true.mean()) ** 2))

    return {
        "n": int(len(df)),
        "mae": round(mae, 3),
        "rmse": round(float(np.sqrt(np.mean(err ** 2))), 3),
        "bias": round(float(np.mean(err)), 3),          # 正值＝系統性高估
        "r2": round(1 - float(np.sum(err ** 2)) / denom, 3) if denom > 0 else None,
        "baseline_mae": round(base_mae, 3),
        "baseline_rmse": round(float(np.sqrt(np.mean(base_err ** 2))), 3),
        # 相對 persistence 的改善幅度：正值代表模型比「濃度不變」更準
        "skill_vs_baseline_pct": round((base_mae - mae) / base_mae * 100, 1) if base_mae > 0 else None,
        "band_hit_rate": round(float(df["band_hit"].mean()) * 100, 1),
        "coverage_pct": round(float(df["abs_error"].le(5).mean()) * 100, 1),  # 誤差 ≤5 µg/m³ 的比例
    }


def summarise(scored: pd.DataFrame) -> dict:
    now = pd.Timestamp.now()
    graded = scored[scored["y_true"].notna()]
    # 暖機期（滯後特徵不完整）的預測一律不列入成效統計。
    # 刻意不做「沒有正式資料就退回用暖機資料」的 fallback——那會讓上線頭 25 小時
    # 的數字看起來像正式成效，但那些預測的滯後特徵是補值來的，不能拿來代表模型表現。
    usable = graded[~graded["warmup"].astype(bool)]

    summary: dict = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pending": int(scored["y_true"].isna().sum()),
        "warmup_excluded": int(len(graded) - len(usable)),
        "warmup_only": bool(usable.empty and not graded.empty),
        "horizons": {},
    }

    for horizon, grp in usable.groupby("horizon_h"):
        per_window_pm25 = {}
        per_window_ox = {}
        for label, days in WINDOWS.items():
            subset = grp if days is None else grp[grp["target_time"] >= now - pd.Timedelta(days=days)]
            per_window_pm25[label] = _metrics(subset)
            # OX 成效：暫時借用 _metrics，替換誤差欄位
            ox_subset = subset.rename(columns={
                "ox_error": "error",
                "ox_abs_error": "abs_error",
                "ox_baseline_error": "baseline_error",
                "ox_baseline_abs_error": "baseline_abs_error",
                "y_true_ox": "y_true",
            }).copy()
            # band_hit 對 OX 不適用，補個假欄位讓 _metrics 不炸
            if "band_hit" not in ox_subset.columns:
                ox_subset["band_hit"] = np.nan
            per_window_ox[label] = _metrics(ox_subset) if ox_subset["y_true"].notna().any() else None

        summary["horizons"][str(int(horizon))] = {
            "pm25": per_window_pm25,
            "ox": per_window_ox,
        }

    return summary


def latest_status(station: str, scored: pd.DataFrame) -> dict:
    obs = pd.read_csv(OBSERVATIONS_CSV, encoding="utf-8-sig", parse_dates=["publishtime"])
    obs = obs[obs["sitename"] == station].sort_values("publishtime")
    latest = obs.dropna(subset=["pm2.5"]).iloc[-1] if obs["pm2.5"].notna().any() else None
    # OX 最新實測（o3 + no2）
    obs["ox_actual"] = pd.to_numeric(obs["o3"], errors="coerce") + pd.to_numeric(obs["no2"], errors="coerce")
    latest_ox = obs.dropna(subset=["ox_actual"]).iloc[-1] if obs["ox_actual"].notna().any() else None

    # 現況卡片只顯示「最新一次執行」產生的預報，不要把歷史上還沒驗證到的舊預測也撈進來
    upcoming = scored[scored["base_time"] == scored["base_time"].max()].sort_values("horizon_h")
    forecasts = [
        {
            "target_time": str(r["target_time"]),
            "horizon_h": int(r["horizon_h"]),
            "y_pred": float(r["pm25_pred"]),
            "band": aqi_band(float(r["pm25_pred"]))[0],
            "color": aqi_band(float(r["pm25_pred"]))[1],
        }
        for _, r in upcoming.iterrows()
        if pd.notna(r.get("pm25_pred"))
    ]

    status = {
        "station": station,
        "observation_rows": int(len(obs)),
        "observation_hours": int(obs["pm2.5"].notna().sum()),
        "latest_obs_time": str(latest["publishtime"]) if latest is not None else None,
        "latest_pm25": float(latest["pm2.5"]) if latest is not None else None,
        "latest_band": aqi_band(float(latest["pm2.5"]))[0] if latest is not None else "無資料",
        "latest_color": aqi_band(float(latest["pm2.5"]))[1] if latest is not None else "#94A3B8",
        "latest_ox": float(latest_ox["ox_actual"]) if latest_ox is not None else None,
        "forecasts": forecasts,
        "warmup": bool(scored["warmup"].astype(bool).iloc[-1]) if not scored.empty else True,
    }
    if MODEL_META_JSON.exists():
        status["model"] = json.loads(MODEL_META_JSON.read_text(encoding="utf-8"))
    return status


def main(station: str = TARGET_STATION) -> dict:
    scored = build_scores(station)
    if scored.empty:
        logger.warning("尚無預測可評分")
        return {}

    payload = summarise(scored)
    payload["status"] = latest_status(station, scored)

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    METRICS_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(
        "已評分 %d 筆（待驗證 %d 筆）-> %s",
        int(scored["y_true"].notna().sum()), payload["pending"], SCORES_CSV,
    )
    for h, targets in payload["horizons"].items():
        m_pm25 = targets.get("pm25", {}).get("all")
        m_ox   = targets.get("ox", {}).get("all")
        if m_pm25:
            logger.info(
                "  +%sh PM2.5 全期：n=%d MAE=%.2f RMSE=%.2f R²=%s 勝過基準 %s%%",
                h, m_pm25["n"], m_pm25["mae"], m_pm25["rmse"], m_pm25["r2"],
                m_pm25["skill_vs_baseline_pct"],
            )
        if m_ox:
            logger.info(
                "  +%sh OX    全期：n=%d MAE=%.2f RMSE=%.2f R²=%s 勝過基準 %s%%",
                h, m_ox["n"], m_ox["mae"], m_ox["rmse"], m_ox["r2"],
                m_ox["skill_vs_baseline_pct"],
            )
    return payload


if __name__ == "__main__":
    main()
