import os

# 站點設定
STATION = "桃園"

# CSV 遠端資料源
CSV_URL = os.getenv(
    "CSV_URL",
    "https://raw.githubusercontent.com/kachio-lab/Taoyuan-Air-Quality-Monitor/main/data/monitor/predictions.csv",
)

# LINE Messaging API 設定
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.getenv("LINE_USER_ID", "")

# 警戒閾值
PM25_ALERT = float(os.getenv("ALERT_PM25_THRESHOLD", "35.5"))
OX_ALERT = float(os.getenv("ALERT_OX_THRESHOLD", "60.0"))