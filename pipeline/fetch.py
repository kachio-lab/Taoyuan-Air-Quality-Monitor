"""每小時抓取即時觀測，累加寫入 data/monitor/observations.csv。

資料來源：
- 環境部 aqx_p_432（AQI 即時值）：污染物 + 風速風向，不含氣象
- 中央氣象署 O-A0001-001：補 amb_temp / rh（依經緯度配對最近的有效氣象站，結果快取）

設計原則：氣象是輔助欄位，CWA 失敗只警告不中斷；同測站同整點重複抓取時以最新一筆覆蓋。
用法：python -m pipeline.fetch
"""
from __future__ import annotations

import json
import logging
import math
import time

import pandas as pd
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from pipeline.settings import (
    API_FIELD_MAP,
    BASE_COLUMNS,
    CWA_API_KEY,
    CWA_API_URL,
    MAX_RETRIES,
    MAX_STATION_MATCH_DISTANCE_KM,
    MOENV_API_KEY,
    MOENV_API_URL,
    MONITOR_DIR,
    OBSERVATIONS_CSV,
    REQUEST_TIMEOUT,
    SCHEMA_COLUMNS,
    STATION_MAP_JSON,
    STATIONS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- 環境部 AQI
def fetch_aqi_records() -> list[dict]:
    if not MOENV_API_KEY:
        raise RuntimeError("缺少 MOENV_API_KEY（本機放 .env，GitHub 放 repository secret）")

    params = {"api_key": MOENV_API_KEY, "format": "JSON"}
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(MOENV_API_URL, params=params, timeout=REQUEST_TIMEOUT, verify=False)
            resp.raise_for_status()
            payload = resp.json()
            return payload if isinstance(payload, list) else payload.get("records", [])
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            logger.warning("第 %d 次呼叫環境部 API 失敗：%s", attempt, exc)
            time.sleep(2 * attempt)
    raise RuntimeError(f"環境部 API 連續失敗 {MAX_RETRIES} 次：{last_error}")


def to_dataframe(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    if df.empty or "sitename" not in df.columns:
        logger.warning("API 回傳空資料或格式不符")
        return pd.DataFrame(columns=SCHEMA_COLUMNS)

    df = df[df["sitename"].isin(STATIONS)].copy()
    if df.empty:
        logger.warning("此次拉取無桃園地區測站資料")
        return pd.DataFrame(columns=SCHEMA_COLUMNS)

    keep = {k: v for k, v in API_FIELD_MAP.items() if k in df.columns}
    df = df[list(keep)].rename(columns=keep)

    df["publishtime"] = pd.to_datetime(df["publishtime"], errors="coerce").dt.floor("h")
    for col in df.columns:
        if col not in ("sitename", "publishtime"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["publishtime"])


# ------------------------------------------------------------ 中央氣象署氣象
def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def fetch_cwa_stations() -> pd.DataFrame:
    if not CWA_API_KEY:
        raise RuntimeError("缺少 CWA_API_KEY")

    resp = requests.get(
        CWA_API_URL,
        params={"Authorization": CWA_API_KEY, "format": "JSON"},
        timeout=REQUEST_TIMEOUT,
        verify=False,
    )
    resp.raise_for_status()
    stations = resp.json().get("records", {}).get("Station", [])
    if not stations:
        raise RuntimeError("CWA API 無測站資料，請確認授權碼")

    rows = []
    for s in stations:
        coords = s.get("GeoInfo", {}).get("Coordinates", [])
        wgs84 = next((c for c in coords if c.get("CoordinateName") == "WGS84"), None)
        if not wgs84:
            continue
        try:
            lat, lon = float(wgs84["StationLatitude"]), float(wgs84["StationLongitude"])
        except (TypeError, ValueError, KeyError):
            continue
        we = s.get("WeatherElement", {})
        rows.append({
            "cwa_station": s.get("StationName"),
            "lat": lat,
            "lon": lon,
            "obs_time": s.get("ObsTime", {}).get("DateTime"),
            "amb_temp": we.get("AirTemperature"),
            "rh": we.get("RelativeHumidity"),
        })

    df = pd.DataFrame(rows)
    for col in ("amb_temp", "rh"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
        df.loc[df[col] < -90, col] = pd.NA  # CWA 以 -99/-999 表示缺測
    return df


def build_station_mapping(aqi_df: pd.DataFrame, weather: pd.DataFrame) -> dict:
    """只在「確實有氣溫觀測」的站點中挑最近的，避免配到沒有溫度感測器的啞站。"""
    valid = weather[weather["amb_temp"].notna()]
    if valid.empty:
        valid = weather

    mapping: dict[str, dict] = {}
    for _, row in aqi_df.drop_duplicates("sitename").iterrows():
        name, lat, lon = row["sitename"], row.get("latitude"), row.get("longitude")
        if pd.isna(lat) or pd.isna(lon):
            logger.warning("測站 %s 缺經緯度，跳過氣象配對", name)
            continue
        dist = valid.apply(lambda r: _haversine_km(lat, lon, r["lat"], r["lon"]), axis=1)
        idx = dist.idxmin()
        if dist[idx] > MAX_STATION_MATCH_DISTANCE_KM:
            logger.warning("測站 %s 最近氣象站 %.1f km 超過門檻，不配對", name, dist[idx])
            continue
        mapping[name] = {
            "cwa_station": valid.loc[idx, "cwa_station"],
            "distance_km": round(float(dist[idx]), 2),
        }
        logger.info("測站 %s -> 氣象站 %s（%.1f km）", name, mapping[name]["cwa_station"], dist[idx])

    MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    STATION_MAP_JSON.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    return mapping


def enrich_with_weather(df: pd.DataFrame) -> pd.DataFrame:
    for col in ("amb_temp", "rh"):
        if col not in df.columns:
            df[col] = pd.NA
    if df.empty:
        return df

    try:
        weather = fetch_cwa_stations()
        mapping = (
            json.loads(STATION_MAP_JSON.read_text(encoding="utf-8"))
            if STATION_MAP_JSON.exists() else build_station_mapping(df, weather)
        )
        rows = []
        for site, info in mapping.items():
            match = weather[weather["cwa_station"] == info["cwa_station"]]
            if match.empty:
                continue
            m = match.iloc[0]
            rows.append({"sitename": site, "amb_temp": m["amb_temp"], "rh": m["rh"]})
        wdf = pd.DataFrame(rows)
    except Exception as exc:  # noqa: BLE001 氣象為輔助欄位，失敗不中斷主流程
        logger.warning("取得氣象資料失敗，amb_temp/rh 維持缺值：%s", exc)
        return df

    if wdf.empty:
        return df
    return df.drop(columns=["amb_temp", "rh"]).merge(wdf, on="sitename", how="left")


# ---------------------------------------------------------------------- 落地
def append_observations(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        logger.warning("本次無資料可寫入")
        return df

    df = df.copy()
    for col in SCHEMA_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[SCHEMA_COLUMNS].copy()

    MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    if OBSERVATIONS_CSV.exists():
        existing = pd.read_csv(OBSERVATIONS_CSV, encoding="utf-8-sig", parse_dates=["publishtime"])
        combined = pd.concat([existing, df], ignore_index=True)
    else:
        combined = df

    combined["publishtime"] = pd.to_datetime(combined["publishtime"], errors="coerce")
    combined = (
        combined.dropna(subset=["publishtime"])
        .drop_duplicates(subset=["sitename", "publishtime"], keep="last")
        .sort_values(["sitename", "publishtime"])
        .reset_index(drop=True)
    )
    for col in BASE_COLUMNS:
        combined[col] = pd.to_numeric(combined[col], errors="coerce")

    combined.to_csv(OBSERVATIONS_CSV, index=False, encoding="utf-8-sig")
    logger.info("觀測資料累計 %d 列 -> %s", len(combined), OBSERVATIONS_CSV)
    return combined


def main() -> pd.DataFrame:
    logger.info("開始拉取即時空品資料…")
    df = to_dataframe(fetch_aqi_records())
    df = enrich_with_weather(df)
    logger.info(
        "本次取得 %d 站；最新時間 %s",
        len(df), df["publishtime"].max() if not df.empty else "N/A",
    )
    return append_observations(df)


if __name__ == "__main__":
    main()
