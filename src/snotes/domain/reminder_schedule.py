"""Deterministic Asia/Shanghai Reminder R1 schedule calculations."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from src.snotes.domain.reminders import REMINDER_TIMEZONE


SHANGHAI = ZoneInfo(REMINDER_TIMEZONE)


class ScheduleRangeError(ValueError):
    """A valid schedule needs a calendar year this build cannot compute."""

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

CN_A_SHARE_CLOSED_DATES_BY_YEAR = {
    2026: {
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 2, 16),
        date(2026, 2, 17),
        date(2026, 2, 18),
        date(2026, 2, 19),
        date(2026, 2, 20),
        date(2026, 2, 23),
        date(2026, 4, 6),
        date(2026, 5, 1),
        date(2026, 5, 4),
        date(2026, 5, 5),
        date(2026, 6, 19),
        date(2026, 9, 25),
        date(2026, 10, 1),
        date(2026, 10, 2),
        date(2026, 10, 5),
        date(2026, 10, 6),
        date(2026, 10, 7),
    },
}


def today_shanghai(clock) -> date:
    now = clock()
    if now.tzinfo is None:
        now = now.replace(tzinfo=SHANGHAI)
    return now.astimezone(SHANGHAI).date()


def parse_date(value: str) -> date:
    return date.fromisoformat(str(value))


def next_action_date(
    spec: dict,
    *,
    on_or_after: date,
    last_completed_at: str | None = None,
) -> date | None:
    repeat_until = _optional_date(spec.get("repeat_until"))
    candidate = _next_action_date_uncapped(
        spec,
        on_or_after=on_or_after,
        last_completed_at=last_completed_at,
    )
    if candidate is None:
        return None
    if repeat_until is not None and candidate > repeat_until:
        return None
    return candidate


def deadline_for(action_date: date, spec: dict | None) -> date | None:
    if not spec:
        return None
    kind = str(spec.get("type", ""))
    if kind == "same_day":
        return action_date
    if kind == "offset_days":
        return action_date + timedelta(days=int(spec.get("days", 0)))
    if kind == "one_time":
        return parse_date(str(spec["date"]))
    if kind == "monthly_day":
        return _monthly_day_on_or_after(action_date, int(spec["day"]))
    if kind == "yearly_date":
        return date(action_date.year, int(spec["month"]), int(spec["day"]))
    return None


def occurrence_key(action_date: date) -> str:
    return action_date.isoformat()


def is_recurring(spec: dict) -> bool:
    return str(spec.get("type")) != "one_time"


def describe_schedule(spec: dict) -> str:
    kind = str(spec.get("type", ""))
    if kind == "one_time":
        return "一次"
    if kind == "monthly_day":
        return f"每月{int(spec['day'])}日"
    if kind == "yearly_date":
        return f"每年{int(spec['month'])}月{int(spec['day'])}日"
    if kind == "interval_days":
        return f"每{int(spec['days'])}天"
    if kind == "interval_from_completion":
        return f"完成后{int(spec['days'])}天"
    if kind == "nth_weekday":
        nth = int(spec["nth"])
        weekday = _weekday_label(int(spec.get("weekday", 0)))
        if spec.get("month"):
            return f"每年{int(spec['month'])}月第{nth}个{weekday}"
        return f"每月第{nth}个{weekday}"
    if kind == "lunar_yearly":
        return f"每年农历{int(spec['month'])}月{int(spec['day'])}日"
    if kind == "cn_a_share_on_or_after":
        return f"A股每月{int(spec['day'])}日或之后首个交易日"
    return "自定义"


def _next_action_date_uncapped(
    spec: dict,
    *,
    on_or_after: date,
    last_completed_at: str | None,
) -> date | None:
    kind = str(spec.get("type", ""))
    if kind == "one_time":
        candidate = parse_date(str(spec["date"]))
        return candidate if candidate >= on_or_after else None
    if kind == "monthly_day":
        return _next_monthly_day(on_or_after, int(spec["day"]))
    if kind == "yearly_date":
        return _next_yearly_date(on_or_after, int(spec["month"]), int(spec["day"]))
    if kind == "interval_days":
        return _next_interval_date(on_or_after, parse_date(str(spec["anchor_date"])), int(spec["days"]))
    if kind == "interval_from_completion":
        anchor = _completion_date(last_completed_at) or parse_date(str(spec["anchor_date"]))
        candidate = anchor + timedelta(days=int(spec["days"]))
        while candidate < on_or_after:
            candidate += timedelta(days=int(spec["days"]))
        return candidate
    if kind == "nth_weekday":
        return _next_nth_weekday(
            on_or_after,
            int(spec["nth"]),
            _weekday_value(spec["weekday"]),
            int(spec["month"]) if spec.get("month") else None,
        )
    if kind == "lunar_yearly":
        return _next_lunar_yearly(
            on_or_after,
            int(spec["month"]),
            int(spec["day"]),
            bool(spec.get("leap_month", False)),
        )
    if kind == "cn_a_share_on_or_after":
        return _next_market_day(on_or_after, int(spec["day"]))
    raise ValueError(f"unsupported_reminder_schedule:{kind}")


def _optional_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    return parse_date(str(value))


def _completion_date(value: str | None) -> date | None:
    if not value:
        return None
    completed = datetime.fromisoformat(value)
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=SHANGHAI)
    return completed.astimezone(SHANGHAI).date()


def _next_monthly_day(on_or_after: date, day: int) -> date:
    year, month = on_or_after.year, on_or_after.month
    while True:
        candidate = _clamped_month_day(year, month, day)
        if candidate >= on_or_after:
            return candidate
        year, month = _add_month(year, month)


def _monthly_day_on_or_after(anchor: date, day: int) -> date:
    candidate = _clamped_month_day(anchor.year, anchor.month, day)
    if candidate < anchor:
        year, month = _add_month(anchor.year, anchor.month)
        candidate = _clamped_month_day(year, month, day)
    return candidate


def _next_yearly_date(on_or_after: date, month: int, day: int) -> date:
    year = on_or_after.year
    while True:
        candidate = date(year, month, min(day, monthrange(year, month)[1]))
        if candidate >= on_or_after:
            return candidate
        year += 1


def _next_interval_date(on_or_after: date, anchor: date, days: int) -> date:
    if days <= 0:
        raise ValueError("interval_days_must_be_positive")
    candidate = anchor
    if candidate < on_or_after:
        elapsed = (on_or_after - anchor).days
        jumps = elapsed // days
        candidate = anchor + timedelta(days=jumps * days)
        if candidate < on_or_after:
            candidate += timedelta(days=days)
    return candidate


def _next_nth_weekday(
    on_or_after: date,
    nth: int,
    weekday: int,
    month: int | None,
) -> date:
    start_month = month or on_or_after.month
    for year in range(on_or_after.year, on_or_after.year + 100):
        months = (month,) if month is not None else range(start_month, 13)
        for candidate_month in months:
            try:
                candidate = _nth_weekday(year, candidate_month, nth, weekday)
            except ValueError:
                continue
            if candidate >= on_or_after:
                return candidate
        start_month = 1
    raise ValueError("nth_weekday_not_found_in_supported_range")


def _nth_weekday(year: int, month: int, nth: int, weekday: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    candidate = first + timedelta(days=offset + (nth - 1) * 7)
    if candidate.month != month:
        raise ValueError("nth_weekday_out_of_month")
    return candidate


def _next_market_day(on_or_after: date, day: int) -> date:
    year, month = on_or_after.year, on_or_after.month
    while True:
        candidate = _clamped_month_day(year, month, day)
        while not is_cn_a_share_trading_day(candidate):
            candidate += timedelta(days=1)
        if candidate >= on_or_after:
            return candidate
        year, month = _add_month(year, month)


def is_cn_a_share_trading_day(value: date) -> bool:
    try:
        closed_dates = CN_A_SHARE_CLOSED_DATES_BY_YEAR[value.year]
    except KeyError as error:
        raise ScheduleRangeError(
            f"cn_a_share_calendar_year_not_supported:{value.year}"
        ) from error
    if value.weekday() >= 5:
        return False
    if value in closed_dates:
        return False
    return True


def _add_month(year: int, month: int) -> tuple[int, int]:
    month += 1
    if month == 13:
        return year + 1, 1
    return year, month


def _clamped_month_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, monthrange(year, month)[1]))


def _weekday_value(value: object) -> int:
    if isinstance(value, int):
        return value
    return WEEKDAYS[str(value).casefold()]


def _weekday_label(value: int) -> str:
    return ("周一", "周二", "周三", "周四", "周五", "周六", "周日")[value]


LUNAR_INFO = [
    0x04BD8, 0x04AE0, 0x0A570, 0x054D5, 0x0D260, 0x0D950, 0x16554, 0x056A0,
    0x09AD0, 0x055D2, 0x04AE0, 0x0A5B6, 0x0A4D0, 0x0D250, 0x1D255, 0x0B540,
    0x0D6A0, 0x0ADA2, 0x095B0, 0x14977, 0x04970, 0x0A4B0, 0x0B4B5, 0x06A50,
    0x06D40, 0x1AB54, 0x02B60, 0x09570, 0x052F2, 0x04970, 0x06566, 0x0D4A0,
    0x0EA50, 0x06E95, 0x05AD0, 0x02B60, 0x186E3, 0x092E0, 0x1C8D7, 0x0C950,
    0x0D4A0, 0x1D8A6, 0x0B550, 0x056A0, 0x1A5B4, 0x025D0, 0x092D0, 0x0D2B2,
    0x0A950, 0x0B557, 0x06CA0, 0x0B550, 0x15355, 0x04DA0, 0x0A5D0, 0x14573,
    0x052D0, 0x0A9A8, 0x0E950, 0x06AA0, 0x0AEA6, 0x0AB50, 0x04B60, 0x0AAE4,
    0x0A570, 0x05260, 0x0F263, 0x0D950, 0x05B57, 0x056A0, 0x096D0, 0x04DD5,
    0x04AD0, 0x0A4D0, 0x0D4D4, 0x0D250, 0x0D558, 0x0B540, 0x0B6A0, 0x195A6,
    0x095B0, 0x049B0, 0x0A974, 0x0A4B0, 0x0B27A, 0x06A50, 0x06D40, 0x0AF46,
    0x0AB60, 0x09570, 0x04AF5, 0x04970, 0x064B0, 0x074A3, 0x0EA50, 0x06B58,
    0x055C0, 0x0AB60, 0x096D5, 0x092E0, 0x0C960, 0x0D954, 0x0D4A0, 0x0DA50,
    0x07552, 0x056A0, 0x0ABB7, 0x025D0, 0x092D0, 0x0CAB5, 0x0A950, 0x0B4A0,
    0x0BAA4, 0x0AD50, 0x055D9, 0x04BA0, 0x0A5B0, 0x15176, 0x052B0, 0x0A930,
    0x07954, 0x06AA0, 0x0AD50, 0x05B52, 0x04B60, 0x0A6E6, 0x0A4E0, 0x0D260,
    0x0EA65, 0x0D530, 0x05AA0, 0x076A3, 0x096D0, 0x04BD7, 0x04AD0, 0x0A4D0,
    0x1D0B6, 0x0D250, 0x0D520, 0x0DD45, 0x0B5A0, 0x056D0, 0x055B2, 0x049B0,
    0x0A577, 0x0A4B0, 0x0AA50, 0x1B255, 0x06D20, 0x0ADA0, 0x14B63,
]
LUNAR_FIRST_YEAR = 1900
LUNAR_LAST_YEAR = LUNAR_FIRST_YEAR + len(LUNAR_INFO) - 1


def _next_lunar_yearly(
    on_or_after: date,
    month: int,
    day: int,
    leap_month: bool,
) -> date:
    for year in range(on_or_after.year, LUNAR_LAST_YEAR + 1):
        candidate = lunar_to_solar(year, month, day, leap_month=leap_month)
        if candidate >= on_or_after:
            return candidate
    raise ScheduleRangeError("lunar_year_out_of_supported_range")


def lunar_to_solar(year: int, month: int, day: int, *, leap_month: bool = False) -> date:
    if year < LUNAR_FIRST_YEAR or year > LUNAR_LAST_YEAR:
        raise ScheduleRangeError("lunar_year_out_of_supported_range")
    offset = sum(_lunar_year_days(y) for y in range(LUNAR_FIRST_YEAR, year))
    leap = _leap_month(year)
    for lunar_month in range(1, month):
        offset += _month_days(year, lunar_month)
        if leap == lunar_month:
            offset += _leap_days(year)
    if leap_month:
        if leap != month:
            raise ValueError("requested_lunar_leap_month_not_present")
        offset += _month_days(year, month)
    offset += day - 1
    return date(1900, 1, 31) + timedelta(days=offset)


def _lunar_year_days(year: int) -> int:
    total = 348
    info = LUNAR_INFO[year - 1900]
    for bit in (0x8000, 0x4000, 0x2000, 0x1000, 0x800, 0x400, 0x200, 0x100, 0x80, 0x40, 0x20, 0x10):
        if info & bit:
            total += 1
    return total + _leap_days(year)


def _leap_days(year: int) -> int:
    if _leap_month(year):
        return 30 if (LUNAR_INFO[year - 1900] & 0x10000) else 29
    return 0


def _leap_month(year: int) -> int:
    return LUNAR_INFO[year - 1900] & 0xF


def _month_days(year: int, month: int) -> int:
    return 30 if (LUNAR_INFO[year - 1900] & (0x10000 >> month)) else 29
