"""共用設定：測站清單、API 規格、欄位對照（依據專案計畫書第五、八章）"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ---- 路徑 ----
ROOT_DIR = Path(__file__).resolve().parent.parent
RAW_HISTORICAL_DIR = ROOT_DIR / "data" / "raw_historical"      # 原始寬表 CSV（桃園_2025.csv 等）
REALTIME_RAW_DIR = ROOT_DIR / "data" / "realtime_raw"           # 每次 API 拉取的原始 JSON 存檔
PROCESSED_DIR = ROOT_DIR / "data" / "processed"                 # 整理後的長表資料

# ---- 桃園地區 5 個核心測站（計畫書一、二章） ----
TAOYUAN_STATIONS = ["桃園", "平鎮", "大園", "觀音", "龍潭"]

STATION_GROUPS = {
    "沿海工業測站": ["大園", "觀音"],
    "都會交通測站": ["桃園", "平鎮"],
    "高海拔背景測站": ["龍潭"],
}

# ---- 環境部即時 API（計畫書第八章） ----
# 注意：aqx_p_432（空氣品質指標）為精簡版，實測不含 AMB_TEMP / RH / RAINFALL 等氣象欄位，
# 僅有 sitename, aqi, pollutant, so2, co, o3, pm10, pm2.5, no2, nox, no,
# wind_speed, wind_direc, longitude, latitude, publishtime 等。
# 氣象欄位需另外整合中央氣象署 API 取得（見下方 CWA 設定，對應計畫書第八章註記）。
MOENV_API_URL = "https://data.moenv.gov.tw/api/v2/aqx_p_432"
MOENV_API_KEY = os.getenv("MOENV_API_KEY", "")

# ---- 中央氣象署（CWA）開放資料平台：現在天氣觀測報告（有人氣象站） ----
# 用於補齊 aqx_p_432 缺少的 AMB_TEMP（氣溫）、RH（相對濕度）、RAINFALL（雨量）。
# 申請網址: https://opendata.cwa.gov.tw/ （會員中心 -> API 授權碼）
CWA_API_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0001-001"
CWA_API_KEY = os.getenv("CWA_API_KEY", "")

# 每個空品測站最多允許匹配到幾公里內的氣象站，超過視為無合理匹配（資料品質防呆）
MAX_STATION_MATCH_DISTANCE_KM = 15

# ---- M14 模型特徵欄位（計畫書第七、八章）----
# API 回傳欄位 -> 專案內部標準欄位名稱
API_FIELD_MAP = {
    "sitename": "測站",
    "publishtime": "日期",
    "co": "CO",
    "no2": "NO2",
    "no": "NO",
    "nox": "NOx",
    "so2": "SO2",
    "o3": "O3",
    "pm10": "PM10",
    "pm2.5": "PM2.5",
    "wind_speed": "WIND_SPEED",
    "wind_direc": "WIND_DIREC",
    "longitude": "經度",
    "latitude": "緯度",
}

M14_FEATURE_COLUMNS = [
    "CO", "NO2", "NO", "NOx", "SO2", "O3", "PM10",
    "AMB_TEMP", "RH", "RAINFALL", "WIND_SPEED", "WIND_DIREC",
    "day_of_year", "hour_of_day",
]

TARGET_COLUMN = "PM2.5"

# 歷史寬表欄位（計畫書五-2）：測站 | 日期 | 測項 | 00~23 | Unnamed: 27
HOURLY_COLUMNS = [f"{h:02d}" for h in range(24)]
