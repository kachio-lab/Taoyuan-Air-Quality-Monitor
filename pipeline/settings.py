"""自動化管線的共用設定。

與 src/config.py 分開：src/ 是課堂教材版本（手動執行），
pipeline/ 是要部署到 GitHub Actions 的自動化版本，刻意做成自給自足、
不依賴 src/，這樣 Actions 只需要 pipeline/ 就能跑。
"""
from __future__ import annotations

import os
from pathlib import Path

try:  # 本機開發時讀 .env；GitHub Actions 用 secrets 注入環境變數
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - Actions 上不一定裝 dotenv
    pass

ROOT_DIR = Path(__file__).resolve().parent.parent

# ---- 路徑 ----
RAW_HISTORICAL_DIR = ROOT_DIR / "data" / "raw_historical"  # 原始歷史 CSV（寬表 + 長表），只有 build_history.py 讀
MONITOR_DIR = ROOT_DIR / "data" / "monitor"
MODEL_DIR = ROOT_DIR / "models"
DOCS_DIR = ROOT_DIR / "docs"

HISTORY_CSV = MONITOR_DIR / "history.csv"            # 歷史訓練資料（2025 + 2026 已知資料）
OBSERVATIONS_CSV = MONITOR_DIR / "observations.csv"  # 上線後每小時累積的實際觀測
PREDICTIONS_CSV = MONITOR_DIR / "predictions.csv"    # 每小時產生的預測
SCORES_CSV = MONITOR_DIR / "scores.csv"              # 預測對上實際之後的評分
STATION_MAP_JSON = MONITOR_DIR / "station_weather_mapping.json"
MODEL_META_JSON = MODEL_DIR / "metadata.json"
METRICS_JSON = DOCS_DIR / "metrics.json"
DASHBOARD_HTML = DOCS_DIR / "index.html"

# ---- 監控對象 ----
STATIONS = ["桃園", "平鎮", "大園", "觀音", "龍潭"]   # 一併抓取，之後要擴充監控站別很方便
TARGET_STATION = os.getenv("TARGET_STATION", "桃園")  # 本專案的即時監控主角
TARGET_COLUMN = "pm2.5"
TARGET_COLUMNS = ["pm2.5", "ox"] 
HORIZONS = [1, 3]                                     # 預測未來第 1、3 小時

# ---- API ----
MOENV_API_URL = "https://data.moenv.gov.tw/api/v2/aqx_p_432"
MOENV_API_KEY = os.getenv("MOENV_API_KEY", "")
CWA_API_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0001-001"
CWA_API_KEY = os.getenv("CWA_API_KEY", "")
MAX_STATION_MATCH_DISTANCE_KM = 15
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3

# ---- 選用：LINE 推播（未設定就自動略過）----
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.getenv("LINE_USER_ID", "")
ALERT_PM25_THRESHOLD = float(os.getenv("ALERT_PM25_THRESHOLD", "35.5"))  # 對紅色警示等級
ALERT_OX_THRESHOLD = float(os.getenv("ALERT_OX_THRESHOLD", "60.0"))
# ---- 資料 schema ----
# 訓練資料與即時資料共用的標準欄位（刻意取兩邊的交集，確保上線後算得出同一組特徵）
BASE_COLUMNS = [
    "pm2.5", "pm10", "o3", "co", "so2", "no", "no2", "nox",
    "wind_speed", "wind_direc", "amb_temp", "rh",
]
SCHEMA_COLUMNS = ["sitename", "publishtime"] + BASE_COLUMNS

# 歷史寬表（環境部下載檔）用的測項名稱
WIDE_ITEM_MAP = {
    "PM2.5": "pm2.5", "PM10": "pm10", "O3": "o3", "CO": "co", "SO2": "so2",
    "NO": "no", "NO2": "no2", "NOx": "nox", "WIND_SPEED": "wind_speed",
    "WIND_DIREC": "wind_direc", "AMB_TEMP": "amb_temp", "RH": "rh",
}

# 即時 API 欄位 -> 標準欄位
API_FIELD_MAP = {
    "sitename": "sitename", "publishtime": "publishtime",
    "pm2.5": "pm2.5", "pm10": "pm10", "o3": "o3", "co": "co", "so2": "so2",
    "no": "no", "no2": "no2", "nox": "nox",
    "wind_speed": "wind_speed", "wind_direc": "wind_direc",
    "longitude": "longitude", "latitude": "latitude",
}

# PM2.5 空氣品質指標分級（環境部 AQI 對照，單位 µg/m³，24 小時平均值分界）
AQI_BANDS = [
    (0.0, 15.4, "良好", "#22C55E"),
    (15.5, 35.4, "普通", "#FACC15"),
    (35.5, 54.4, "對敏感族群不健康", "#F97316"),
    (54.5, 150.4, "對所有族群不健康", "#EF4444"),
    (150.5, 250.4, "非常不健康", "#A855F7"),
    (250.5, 10_000.0, "危害", "#7F1D1D"),
]


def aqi_band(value: float | None) -> tuple[str, str]:
    """回傳 (等級名稱, 色碼)"""
    if value is None or value != value:  # NaN
        return "無資料", "#94A3B8"
    for low, high, name, color in AQI_BANDS:
        if low <= value <= high:
            return name, color
    return "危害", "#7F1D1D"
