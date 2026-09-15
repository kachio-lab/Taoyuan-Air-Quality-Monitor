from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from csv_reader import get_latest_taoyuan_data
from evaluator import evaluate
from notifier import send_alert_report, send_daily_report

# 警戒紀錄鎖：{"2026-09-15": {"PM2.5": False, "OX": False}}
alert_tracker = {}


def _clean_and_get_tracker(today_str: str) -> dict:
    """清理舊紀錄並回傳當天的狀態 dict"""
    global alert_tracker
    alert_tracker = {k: v for k, v in alert_tracker.items() if k == today_str}
    if today_str not in alert_tracker:
        alert_tracker[today_str] = {"PM2.5": False, "OX": False}
    return alert_tracker[today_str]


def job_daily_report():
    """定時報告 Job (06:00, 16:00)"""
    data = get_latest_taoyuan_data()
    if not data:
        return

    eval_res = evaluate(data)
    send_daily_report(data, eval_res)

    # 抑制重複推播：若當下有超標，標記 today tracker，防止 10 分鐘後的 Alert Job 重複發送
    today_str = datetime.now().strftime("%Y-%m-%d")
    tracker = _clean_and_get_tracker(today_str)

    if eval_res["pm25_is_alert"]:
        tracker["PM2.5"] = True
    if eval_res["ox_is_alert"]:
        tracker["OX"] = True


def job_check_alert():
    """10 分鐘巡檢 Job"""
    data = get_latest_taoyuan_data()
    if not data:
        return

    eval_res = evaluate(data)
    today_str = datetime.now().strftime("%Y-%m-%d")
    tracker = _clean_and_get_tracker(today_str)

    trigger_alerts = []

    if eval_res["pm25_is_alert"] and not tracker["PM2.5"]:
        trigger_alerts.append("PM2.5")
        tracker["PM2.5"] = True

    if eval_res["ox_is_alert"] and not tracker["OX"]:
        trigger_alerts.append("OX")
        tracker["OX"] = True

    if trigger_alerts:
        send_alert_report(data, eval_res, trigger_alerts)


def start_scheduler():
    """初始化背景排程器"""
    scheduler = BackgroundScheduler(timezone="Asia/Taipei")

    # 定時 Job
    scheduler.add_job(job_daily_report, "cron", hour=6, minute=0, id="daily_6am")
    scheduler.add_job(job_daily_report, "cron", hour=16, minute=0, id="daily_4pm")

    # 10 分鐘巡檢 Job
    scheduler.add_job(job_check_alert, "interval", minutes=10, id="check_alert")

    scheduler.start()
    print("[APScheduler 啟動成功]")