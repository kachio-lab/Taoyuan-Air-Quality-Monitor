import io
import pandas as pd
import requests
from config import CSV_URL, STATION


def get_latest_taoyuan_data() -> dict | None:
    """從 CSV_URL 讀取 predictions.csv，篩選桃園站最新一筆 run_at 並解析雙時距數據"""
    try:
        response = requests.get(CSV_URL, timeout=10)
        if response.status_code != 200:
            print(f"[錯誤] 無法取得 CSV 檔案，HTTP 狀態碼: {response.status_code}")
            return None

        df = pd.read_csv(io.StringIO(response.text))

        # 欄位名稱正規化
        df.columns = [c.strip().lower() for c in df.columns]

        # 篩選桃園站
        station_col = "station" if "station" in df.columns else "sitename"
        df = df[df[station_col] == STATION]

        if df.empty:
            print(f"[警告] CSV 中找不到 {STATION} 站資料")
            return None

        # 轉為 datetime 並找出最新的 run_at
        df["run_at"] = pd.to_datetime(df["run_at"])
        latest_run_at = df["run_at"].max()
        latest_df = df[df["run_at"] == latest_run_at]

        # 分別抓取 horizon_h = 1 與 3 的列
        h1_row = latest_df[latest_df["horizon_h"] == 1]
        h3_row = latest_df[latest_df["horizon_h"] == 3]

        if h1_row.empty or h3_row.empty:
            print("[警告] 找不到完整 horizon_h 1 與 3 的預測數據")
            return None

        h1 = h1_row.iloc[0]
        h3 = h3_row.iloc[0]

        # 格式化 base_time
        base_time_dt = pd.to_datetime(h1["base_time"])
        formatted_base_time = base_time_dt.strftime("%Y-%m-%d %H:%M")

        return {
            "base_time": formatted_base_time,
            "h1_pm25": float(h1.get("pm25_pred", 0.0)),
            "h3_pm25": float(h3.get("pm25_pred", 0.0)),
            "h1_ox": float(h1.get("ox_pred", 0.0)),
            "h3_ox": float(h3.get("ox_pred", 0.0)),
            "warmup": bool(h1.get("warmup", False)),
        }

    except Exception as e:
        print(f"[解析 CSV 失敗]: {e}")
        return None