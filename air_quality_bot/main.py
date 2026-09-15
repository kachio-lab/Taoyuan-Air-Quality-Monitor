import os
from flask import Flask
from scheduler import start_scheduler

app = Flask(__name__)


@app.route("/")
@app.route("/health")
def health_check():
    """回應 UptimeRobot HTTP 200 健康檢查"""
    return "Air Quality Bot SDD v2.0 is Healthy!", 200


if __name__ == "__main__":
    # 啟動背景排程器
    start_scheduler()

    # 監聽 Port
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)