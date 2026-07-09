from datetime import datetime
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def now_shanghai_iso() -> str:
    return now_shanghai().isoformat(timespec="seconds")
