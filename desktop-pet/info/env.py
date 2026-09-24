"""环境信息模块：本地时间 + 联网获取地点与天气。

- 时间型提醒：根据本地时间判断，无需联网。
- 天气提醒：城市可手动配置，否则按 IP 自动定位，再拉取 wttr.in 天气。

所有网络操作都应在后台线程调用（get_city / fetch_weather），
失败时返回 None 静默降级，绝不阻塞 GUI。
"""

import json
import threading
import time
import urllib.parse

import config

_GEO_URLS = [
    # 免费 IP 定位（中文地区名优先）
    "http://ip-api.com/json/?lang=zh-CN&fields=status,message,country,regionName,city",
    # 备选定位（英文城市名）
    "https://api.ip.sb/geoip",
]
_WEATHER_URL = "https://wttr.in/{city}?format=j1&lang=zh"
_TIMEOUT = 8
_SESSION = None


def _http_get(url, timeout=_TIMEOUT):
    """统一走 curl_cffi（模拟浏览器 TLS 指纹，兼容 Cloudflare 站点）。"""
    global _SESSION
    try:
        if _SESSION is None:
            from curl_cffi import requests as cffi_requests
            _SESSION = cffi_requests.Session(
                impersonate="chrome131", timeout=timeout)
        resp = _SESSION.get(url)
        return resp.text
    except Exception:
        return ""

# wttr.in weatherCode -> 中文天气描述（lang 对英文城市名常不生效，做兜底）
_WEATHER_CODE_ZH = {
    "113": "晴", "116": "少云", "119": "多云", "122": "阴",
    "143": "有雾", "248": "有雾", "260": "有浓雾",
    "176": "小雨", "263": "毛毛雨", "266": "小雨", "281": "冻毛毛雨",
    "284": "冻毛毛雨", "293": "小雨", "296": "小雨", "299": "中雨",
    "302": "中雨", "305": "大雨", "308": "大雨",
    "311": "冻雨", "314": "冻雨",
    "179": "小雪", "182": "雨夹雪", "185": "冻毛毛雨",
    "227": "吹雪", "230": "暴风雪", "317": "小雨夹雪",
    "320": "中雨夹雪", "323": "小雪", "326": "小雪", "329": "小到中雪",
    "332": "中雪", "335": "小到大雪", "338": "大雪",
    "350": "冰粒", "374": "小冰粒", "377": "大冰粒",
    "353": "阵雨", "356": "阵雨", "359": "大阵雨",
    "362": "阵雨夹雪", "365": "阵雨夹雪",
    "368": "阵雪", "371": "阵雪",
    "200": "雷阵雨", "386": "雷阵雨", "389": "雷阵雨",
    "392": "雷阵雪", "395": "雷阵雪",
}


def get_city():
    """按 IP 定位城市名；失败返回 None。"""
    for url in _GEO_URLS:
        try:
            data = json.loads(_http_get(url))
            if data.get("status") == "success":
                city = data.get("city") or data.get("regionName")
                if city:
                    return city
            elif data.get("city"):
                return data["city"]
        except Exception:
            continue
    return None


def fetch_weather(city):
    """按城市拉取天气，返回精简 dict；失败返回 None。

    返回: {city, temp, desc, maxtemp, mintemp, max_rain_chance, wind}
    """
    try:
        raw = json.loads(_http_get(
            _WEATHER_URL.format(city=urllib.parse.quote(city))))
    except Exception:
        return None
    try:
        cur = raw["current_condition"][0]
        weather = raw.get("weather") or []
        today = weather[0] if weather else {}
        hourly = today.get("hourly") or []
        chance = max((int(h.get("chanceofrain", 0) or 0) for h in hourly),
                     default=0)

        def _desc(cond):
            code = str(cond.get("weatherCode", ""))
            zh = _WEATHER_CODE_ZH.get(code)
            if zh:
                return zh
            for key in ("lang_zh", "weatherDesc"):
                arr = cond.get(key)
                if arr and arr[0].get("value"):
                    return arr[0]["value"]
            return ""

        return {
            "city": city,
            "temp": int(round(float(cur.get("temp_C", 0) or 0))),
            "feels": int(round(float(cur.get("FeelsLikeC", 0) or 0))),
            "desc": _desc(cur),
            "code": str(cur.get("weatherCode", "")),
            "maxtemp": today.get("maxtempC", ""),
            "mintemp": today.get("mintempC", ""),
            "max_rain_chance": chance,
            "wind": int(round(float(cur.get("windspeedKmph", 0) or 0))),
        }
    except Exception:
        return None


def weather_category(w):
    """根据天气数据判断提醒类别：rain / hot / cold / wind / None。"""
    if not w:
        return None
    desc = w.get("desc") or ""
    code = str(w.get("code", ""))
    rainy = (("雨" in desc) or ("雪" in desc)
             or (code in ("176", "200", "386", "389", "353", "356",
                          "359", "263", "266", "293", "296", "299",
                          "302", "305", "308")))
    if rainy or w.get("max_rain_chance", 0) >= config.ENV_RAIN_CHANCE:
        return "rain"
    if w.get("temp", 0) >= config.ENV_HOT_TEMP:
        return "hot"
    if w.get("temp", 0) <= config.ENV_COLD_TEMP:
        return "cold"
    if w.get("wind", 0) >= config.ENV_WIND_SPEED:
        return "wind"
    return None


def time_reminder(now):
    """根据当前时间返回应触发的固定时刻提醒类别，或 None。
    now: datetime.datetime。精确到分钟，由调用方每分钟检查一次。"""
    for item in config.ENV_TIME_REMINDERS:
        if now.hour == item["hour"] and now.minute == item["minute"]:
            return item["category"]
    return None


class CachedCity:
    """带缓存与后台刷新的城市定位器（可选，供 GUI 使用）。"""

    def __init__(self, cache_file=None):
        self._cache_file = cache_file
        self._city = None
        self._lock = threading.Lock()
        self._last_try = 0.0

    def get(self, force=False):
        with self._lock:
            if self._city and not force:
                return self._city
            if not force and time.monotonic() - self._last_try < 1800:
                return self._city
            self._last_try = time.monotonic()
            self._city = get_city()
            return self._city