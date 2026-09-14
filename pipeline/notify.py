"""選用的 LINE 推播：PM2.5 或 OX 預測值超過門檻時主動通知。

沒設定 LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID 就自動跳過，不會讓排程失敗。
金鑰一律走環境變數（本機 .env、GitHub repository secrets），不要寫死在程式裡。

用法：python -m pipeline.notify
"""
from __future__ import annotations

import logging

import pandas as pd
import requests

from pipeline.settings import (
    ALERT_OX_THRESHOLD,
    ALERT_PM25_THRESHOLD,
    LINE_CHANNEL_ACCESS_TOKEN,
    LINE_USER_ID,
    PREDICTIONS_CSV,
    TARGET_STATION,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


def load_latest_predictions(station: str) -> pd.DataFrame:
    """讀取最新一筆 run_at 的預測結果。"""
    if not PREDICTIONS_CSV.exists():
        return pd.DataFrame()

    df = pd.read_csv(PREDICTIONS_CSV, encoding="utf-8-sig", parse_dates=["run_at", "target_time"])
    df = df[df["station"] == station]
    if df.empty:
        return df

    latest_run = df["run_at"].max()
    return df[df["run_at"] == latest_run].sort_values("horizon_h")


def pm25_band(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "無資料"
    if value < 15.5:  return "良好 🟢"
    if value < 35.5:  return "普通 🟡"
    if value < 54.5:  return "敏感族群不健康 🟠"
    return "不健康 🔴"


def ox_band(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "無資料"
    if value < 30:   return "良好 🟢"
    if value < 60:   return "普通 🟡"
    return "警戒 🔴"


def build_message(df: pd.DataFrame, station: str) -> str | None:
    """只在任一指標超過門檻時才發訊息。"""
    if df.empty:
        return None

    pm25_alerts = []
    ox_alerts = []

    for _, row in df.iterrows():
        h = int(row["horizon_h"])
        pm25 = row.get("pm25_pred")
        ox   = row.get("ox_pred")
        t    = row["target_time"].strftime("%H:%M") if pd.notna(row["target_time"]) else "—"

        if pd.notna(pm25) and pm25 >= ALERT_PM25_THRESHOLD:
            pm25_alerts.append(f"  +{h}h（{t}）：{pm25:.1f} µg/m³ {pm25_band(pm25)}")
        if pd.notna(ox) and ox >= ALERT_OX_THRESHOLD:
            ox_alerts.append(f"  +{h}h（{t}）：{ox:.1f} ppb {ox_band(ox)}")

    if not pm25_alerts and not ox_alerts:
        return None

    lines = [f"⚠️ {station}測站空氣品質警示"]

    if pm25_alerts:
        lines.append(f"\n【PM2.5】門檻 {ALERT_PM25_THRESHOLD} µg/m³")
        lines.extend(pm25_alerts)

    if ox_alerts:
        lines.append(f"\n【OX（臭氧＋二氧化氮）】門檻 {ALERT_OX_THRESHOLD} ppb")
        lines.extend(ox_alerts)

    base_time = df.iloc[0]["run_at"].strftime("%m/%d %H:%M") if pd.notna(df.iloc[0]["run_at"]) else "—"
    lines.append(f"\n預測基準時間：{base_time}")

    return "\n".join(lines)


def build_routine_message(df: pd.DataFrame, station: str) -> str:
    """定時報告：不論有無超標都發，格式簡潔。"""
    if df.empty:
        return f"🌤 {station}測站 — 尚無預測資料"

    lines = [f"🌤 {station}測站空氣品質預報"]
    for _, row in df.iterrows():
        h    = int(row["horizon_h"])
        t    = row["target_time"].strftime("%H:%M") if pd.notna(row["target_time"]) else "—"
        pm25 = row.get("pm25_pred")
        ox   = row.get("ox_pred")
        pm25_str = f"{pm25:.1f} µg/m³ {pm25_band(pm25)}" if pd.notna(pm25) else "—"
        ox_str   = f"{ox:.1f} ppb {ox_band(ox)}"         if pd.notna(ox)   else "—"
        lines.append(f"+{h}h（{t}）PM2.5：{pm25_str}｜OX：{ox_str}")

    return "\n".join(lines)


def push_line(text: str) -> bool:
    resp = requests.post(
        LINE_PUSH_URL,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        },
        json={"to": LINE_USER_ID, "messages": [{"type": "text", "text": text}]},
        timeout=15,
    )
    if resp.status_code == 200:
        logger.info("LINE 推播成功")
        return True
    logger.warning("LINE 推播失敗（%s）：%s", resp.status_code, resp.text)
    return False


def main(routine: bool = False, station: str = TARGET_STATION) -> bool:
    """
    routine=False（預設）：只有超標才推警示訊息
    routine=True：固定推定時報告（無論是否超標）
    """
    if not (LINE_CHANNEL_ACCESS_TOKEN and LINE_USER_ID):
        logger.info("未設定 LINE 金鑰，略過推播")
        return False

    df = load_latest_predictions(station)

    if routine:
        text = build_routine_message(df, station)
        return push_line(text)
    else:
        text = build_message(df, station)
        if not text:
            logger.info("未達警示門檻，不推播")
            return False
        return push_line(text)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--routine", action="store_true", help="定時報告模式（不論是否超標都推）")
    parser.add_argument("--station", default=TARGET_STATION)
    args = parser.parse_args()
    main(routine=args.routine, station=args.station)
