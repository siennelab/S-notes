from datetime import datetime
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
CHINESE_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def now_shanghai_iso() -> str:
    return now_shanghai().isoformat(timespec="seconds")


def format_sidebar_clock(value: datetime | None = None) -> str:
    timestamp = value or now_shanghai()
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=SHANGHAI_TZ)
    timestamp = timestamp.astimezone(SHANGHAI_TZ)
    return (
        f"{timestamp:%H:%M:%S %Y/%m/%d} "
        f"{CHINESE_WEEKDAYS[timestamp.weekday()]}"
    )


def format_display_datetime(value: str) -> str:
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=SHANGHAI_TZ)
    return timestamp.astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M")
