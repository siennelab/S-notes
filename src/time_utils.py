from datetime import datetime
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def now_shanghai_iso() -> str:
    return now_shanghai().isoformat(timespec="seconds")


def format_display_datetime(value: str) -> str:
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=SHANGHAI_TZ)
    return timestamp.astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M")
