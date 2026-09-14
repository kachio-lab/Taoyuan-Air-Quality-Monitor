"""每小時排程的進入點：抓取 -> 預測 -> 評分 -> 更新儀表板 -> （選用）推播。

任一步驟失敗都會印出完整錯誤並以非零狀態碼結束，讓 GitHub Actions 標記成失敗；
但「氣象補值失敗」「LINE 未設定」這類非關鍵狀況只會警告，不中斷流程。

用法：python run_hourly.py
"""
from __future__ import annotations

import logging
import sys
import traceback

from pipeline import dashboard, evaluate, fetch, notify, predict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_hourly")


def main() -> int:
    try:
        fetch.main()
        predict.main()
        evaluate.main()
        dashboard.main()
    except Exception:
        logger.error("每小時流程失敗：\n%s", traceback.format_exc())
        return 1

    try:
        notify.main()  # 推播是加值功能，失敗不影響資料與儀表板
    except Exception as exc:  # noqa: BLE001
        logger.warning("推播步驟失敗（不影響主流程）：%s", exc)

    logger.info("每小時流程完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
