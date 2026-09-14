"""中央氣象署（CWA）有人氣象站爬蟲

用途：補齊環境部 aqx_p_432 API 缺少的氣象欄位（AMB_TEMP 氣溫、RH 相對濕度、RAINFALL 雨量）。
做法：抓取 CWA「現在天氣觀測報告」(O-A0001-001) 全台有人氣象站即時觀測，
      依經緯度找出每個空品測站最近的氣象站，快取此對照表（測站位置固定，不需每次重算），
      之後直接依對照表取值。

CWA API 文件：https://opendata.cwa.gov.tw/dataset/observation/O-A0001-001
（實測 2026-07-20：回傳結構為 records.Station[]，每站含 GeoInfo.Coordinates[WGS84]
 與 WeatherElement.{AirTemperature, RelativeHumidity, Now.Precipitation}，共 874 站）
"""
import json
import logging
import math

import pandas as pd
import requests

from config import (
    CWA_API_URL,
    CWA_API_KEY,
    MAX_STATION_MATCH_DISTANCE_KM,
    PROCESSED_DIR,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15
STATION_MAPPING_CACHE = PROCESSED_DIR / "station_weather_mapping.json"


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def fetch_cwa_stations() -> pd.DataFrame:
    """抓取全台氣象站即時觀測（O-A0001-001），回傳含 站名/經緯度/氣溫/濕度/雨量/觀測時間 的表"""
    if not CWA_API_KEY:
        raise RuntimeError(
            "缺少 CWA_API_KEY，請於 .env 檔設定（申請網址：https://opendata.cwa.gov.tw/）"
        )

    params = {"Authorization": CWA_API_KEY, "format": "JSON"}
    resp = requests.get(CWA_API_URL, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()

    stations = payload.get("records", {}).get("Station", [])
    if not stations:
        raise RuntimeError("CWA API 回傳無測站資料，請確認 API Key 或 dataset id 是否正確")

    rows = []
    for s in stations:
        try:
            coords = s.get("GeoInfo", {}).get("Coordinates", [])
            wgs84 = next((c for c in coords if c.get("CoordinateName") == "WGS84"), coords[0] if coords else {})
            lat = float(wgs84.get("StationLatitude"))
            lon = float(wgs84.get("StationLongitude"))

            we = s.get("WeatherElement", {})
            rows.append(
                {
                    "cwa_station": s.get("StationName"),
                    "lat": lat,
                    "lon": lon,
                    "obs_time": s.get("ObsTime", {}).get("DateTime"),
                    "AMB_TEMP": we.get("AirTemperature"),
                    "RH": we.get("RelativeHumidity"),
                    "RAINFALL": (we.get("Now") or {}).get("Precipitation"),
                }
            )
        except (TypeError, ValueError, StopIteration):
            continue

    df = pd.DataFrame(rows)
    for col in ("AMB_TEMP", "RH", "RAINFALL"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
        df.loc[df[col] < -90, col] = pd.NA  # CWA 缺測常以 -99 / -999 表示

    return df


def build_station_mapping(aqi_stations: pd.DataFrame, weather_stations: pd.DataFrame) -> dict:
    """依經緯度為每個空品測站找出最近的氣象站，回傳 {測站: cwa_station} 對照表並快取

    部分 CWA 站點（如公路局路況站）沒有氣溫感測器，AMB_TEMP 會是空值，
    因此只在「確實有氣溫觀測值」的站點中挑最近的，避免配到啞站。
    """
    valid_weather_stations = weather_stations[weather_stations["AMB_TEMP"].notna()]
    if valid_weather_stations.empty:
        logger.warning("所有氣象站均無氣溫觀測值，退回使用全部站點")
        valid_weather_stations = weather_stations

    mapping = {}
    for _, row in aqi_stations.iterrows():
        name, lat, lon = row["測站"], row["緯度"], row["經度"]
        if pd.isna(lat) or pd.isna(lon):
            logger.warning("測站 %s 缺少經緯度，無法匹配氣象站", name)
            continue

        weather_stations = valid_weather_stations.copy()
        weather_stations["distance_km"] = weather_stations.apply(
            lambda r: _haversine_km(lat, lon, r["lat"], r["lon"]), axis=1
        )
        nearest = weather_stations.sort_values("distance_km").iloc[0]

        if nearest["distance_km"] > MAX_STATION_MATCH_DISTANCE_KM:
            logger.warning(
                "測站 %s 最近氣象站 %s 距離 %.1f km，超過門檻 %d km，不予匹配",
                name, nearest["cwa_station"], nearest["distance_km"], MAX_STATION_MATCH_DISTANCE_KM,
            )
            continue

        mapping[name] = {
            "cwa_station": nearest["cwa_station"],
            "distance_km": round(nearest["distance_km"], 2),
        }
        logger.info("測站 %s -> 氣象站 %s（%.1f km）", name, nearest["cwa_station"], nearest["distance_km"])

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    STATION_MAPPING_CACHE.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    return mapping


def load_cached_mapping() -> dict | None:
    if STATION_MAPPING_CACHE.exists():
        return json.loads(STATION_MAPPING_CACHE.read_text(encoding="utf-8"))
    return None


def get_weather_features(aqi_stations: pd.DataFrame) -> pd.DataFrame:
    """回傳每個空品測站對應的即時氣象特徵（測站, AMB_TEMP, RH, RAINFALL）

    aqi_stations 需含欄位：測站, 經度, 緯度（由 realtime_crawler 篩選出的當次資料提供）
    """
    weather_stations = fetch_cwa_stations()

    mapping = load_cached_mapping()
    if not mapping:
        mapping = build_station_mapping(aqi_stations.drop_duplicates("測站"), weather_stations)

    rows = []
    for aqi_name, info in mapping.items():
        match = weather_stations[weather_stations["cwa_station"] == info["cwa_station"]]
        if match.empty:
            continue
        m = match.iloc[0]
        rows.append(
            {
                "測站": aqi_name,
                "AMB_TEMP": m["AMB_TEMP"],
                "RH": m["RH"],
                "RAINFALL": m["RAINFALL"],
                "weather_obs_time": m["obs_time"],
            }
        )

    return pd.DataFrame(rows)
