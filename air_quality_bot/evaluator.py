from config import OX_ALERT, PM25_ALERT


def get_pm25_level(value: float) -> str:
    """判斷 PM2.5 指標等級"""
    if value < 15.5:
        return "良好"
    elif value < 35.5:
        return "普通"
    elif value < 54.5:
        return "對敏感族群不健康"
    else:
        return "不健康"


def get_ox_level(value: float) -> str:
    """判斷 OX 指標等級"""
    if value < 30.0:
        return "良好"
    elif value < 60.0:
        return "普通"
    else:
        return "警戒"


def evaluate(data: dict) -> dict:
    """綜合評估各指標等級與是否超標"""
    h1_pm25_lvl = get_pm25_level(data["h1_pm25"])
    h3_pm25_lvl = get_pm25_level(data["h3_pm25"])
    h1_ox_lvl = get_ox_level(data["h1_ox"])
    h3_ox_lvl = get_ox_level(data["h3_ox"])

    pm25_is_alert = (data["h1_pm25"] >= PM25_ALERT) or (data["h3_pm25"] >= PM25_ALERT)
    ox_is_alert = (data["h1_ox"] >= OX_ALERT) or (data["h3_ox"] >= OX_ALERT)

    return {
        "h1_pm25_level": h1_pm25_lvl,
        "h3_pm25_level": h3_pm25_lvl,
        "h1_ox_level": h1_ox_lvl,
        "h3_ox_level": h3_ox_lvl,
        "pm25_is_alert": pm25_is_alert,
        "ox_is_alert": ox_is_alert,
    }