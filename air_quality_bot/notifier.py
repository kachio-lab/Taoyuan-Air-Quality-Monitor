import json
from config import LINE_CHANNEL_ACCESS_TOKEN, LINE_USER_ID
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    FlexContainer,
    FlexMessage,
    MessagingApi,
    PushMessageRequest,
    TextMessage,
)

LEVEL_COLORS = {
    "良好": "#22C55E",  # 綠
    "普通": "#FACC15",  # 黃
    "對敏感族群不健康": "#F97316",  # 橘
    "不健康": "#EF4444",  # 紅
    "警戒": "#EF4444",  # 紅
}


def _send_push(message_obj):
    """底層 LINE 訊息發送器"""
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID:
        print(f"[模擬 LINE 推播]: {message_obj}")
        return

    configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        request = PushMessageRequest(to=LINE_USER_ID, messages=[message_obj])
        try:
            line_bot_api.push_message(request)
            print("[LINE 推播成功]")
        except Exception as e:
            print(f"[LINE 推播失敗]: {e}")


def send_daily_report(data: dict, eval_res: dict):
    """發送 Flex Message 定時圖表報告"""
    time_str = data["base_time"].split(" ")[-1] if " " in data["base_time"] else data["base_time"]

    flex_json = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "🌤 桃園站空氣品質預報",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#1E293B",
                },
                {"type": "separator", "margin": "md"},
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "md",
                    "contents": [
                        {"type": "text", "text": "指標", "weight": "bold", "size": "sm", "flex": 2},
                        {"type": "text", "text": "+1h", "weight": "bold", "size": "sm", "align": "center", "flex": 2},
                        {"type": "text", "text": "+3h", "weight": "bold", "size": "sm", "align": "center", "flex": 2},
                    ],
                },
                # PM2.5 列
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "sm",
                    "contents": [
                        {"type": "text", "text": "PM2.5", "size": "sm", "flex": 2, "gravity": "center"},
                        {
                            "type": "box",
                            "layout": "vertical",
                            "flex": 2,
                            "backgroundColor": LEVEL_COLORS.get(eval_res["h1_pm25_level"], "#CCCCCC"),
                            "cornerRadius": "sm",
                            "paddingAll": "xs",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": f"{data['h1_pm25']:.1f}",
                                    "color": "#FFFFFF",
                                    "size": "xs",
                                    "align": "center",
                                    "weight": "bold",
                                }
                            ],
                        },
                        {"type": "text", "text": " ", "flex": 0, "margin": "xs"},
                        {
                            "type": "box",
                            "layout": "vertical",
                            "flex": 2,
                            "backgroundColor": LEVEL_COLORS.get(eval_res["h3_pm25_level"], "#CCCCCC"),
                            "cornerRadius": "sm",
                            "paddingAll": "xs",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": f"{data['h3_pm25']:.1f}",
                                    "color": "#FFFFFF",
                                    "size": "xs",
                                    "align": "center",
                                    "weight": "bold",
                                }
                            ],
                        },
                    ],
                },
                # OX 列
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "sm",
                    "contents": [
                        {"type": "text", "text": "OX", "size": "sm", "flex": 2, "gravity": "center"},
                        {
                            "type": "box",
                            "layout": "vertical",
                            "flex": 2,
                            "backgroundColor": LEVEL_COLORS.get(eval_res["h1_ox_level"], "#CCCCCC"),
                            "cornerRadius": "sm",
                            "paddingAll": "xs",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": f"{data['h1_ox']:.1f}",
                                    "color": "#FFFFFF",
                                    "size": "xs",
                                    "align": "center",
                                    "weight": "bold",
                                }
                            ],
                        },
                        {"type": "text", "text": " ", "flex": 0, "margin": "xs"},
                        {
                            "type": "box",
                            "layout": "vertical",
                            "flex": 2,
                            "backgroundColor": LEVEL_COLORS.get(eval_res["h3_ox_level"], "#CCCCCC"),
                            "cornerRadius": "sm",
                            "paddingAll": "xs",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": f"{data['h3_ox']:.1f}",
                                    "color": "#FFFFFF",
                                    "size": "xs",
                                    "align": "center",
                                    "weight": "bold",
                                }
                            ],
                        },
                    ],
                },
                {"type": "separator", "margin": "md"},
                {
                    "type": "text",
                    "text": f"基準時間：{time_str}",
                    "size": "xs",
                    "color": "#64748B",
                    "margin": "md",
                },
            ],
        },
    }

    container = FlexContainer.from_dict(flex_json)
    message = FlexMessage(alt_text="桃園站空氣品質預報", contents=container)
    _send_push(message)


def send_alert_report(data: dict, eval_res: dict, alert_types: list):
    """發送純文字警戒訊息"""
    time_str = data["base_time"].split(" ")[-1] if " " in data["base_time"] else data["base_time"]
    lines = ["⚠️ 桃園站空氣品質警戒\n"]

    if "PM2.5" in alert_types:
        lines.append("【PM2.5】")
        lines.append(f"  +1h：{data['h1_pm25']:.1f} μg/m³ — {eval_res['h1_pm25_level']}")
        lines.append(f"  +3h：{data['h3_pm25']:.1f} μg/m³ — {eval_res['h3_pm25_level']}\n")

    if "OX" in alert_types:
        lines.append("【OX】")
        lines.append(f"  +1h：{data['h1_ox']:.1f} ppb — {eval_res['h1_ox_level']}")
        lines.append(f"  +3h：{data['h3_ox']:.1f} ppb — {eval_res['h3_ox_level']}\n")

    lines.append(f"基準時間：{time_str}")
    _send_push(TextMessage(text="\n".join(lines).strip()))