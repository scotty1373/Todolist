"""Utility functions for time parsing and formatting."""

from datetime import datetime, date, timedelta
from dateutil import parser as date_parser
from dateutil.relativedelta import relativedelta
import re


def normalize_local_time(value: datetime | None) -> datetime | None:
    """Use the server's local wall time throughout the application."""
    if value is not None and value.utcoffset() is not None:
        try:
            return value.astimezone().replace(tzinfo=None)
        except (OSError, OverflowError) as error:
            raise ValueError("时间超出本地时区可转换的范围") from error
    return value


def parse_iso_time(value: str) -> datetime:
    return normalize_local_time(datetime.fromisoformat(value))


def _is_clock_time(value: str) -> bool:
    pattern = r"(?:\d{1,2}(?::\d{2}){1,2}\s*(?:[ap]m)?|\d{1,2}\s*[ap]m)"
    return bool(re.fullmatch(pattern, value.strip(), re.IGNORECASE))


def parse_time(time_str: str, base_date: date = None, allow_next_day: bool = True) -> datetime:
    """
    Parse time string to datetime.

    Supports:
    - Absolute time: "2026-04-08 18:00", "18:00", "2026/04/08 18:00"
    - Relative time: "tomorrow 18:00", "今天 18:00", "明天 14:00"
    - Time only: "18:00" (uses today's date)
    - Cross-day time: "23:00-05:00" context (if end < start, implies next day)

    Args:
        time_str: Time string to parse
        base_date: Base date for time-only strings (default: today)
        allow_next_day: If True and time < current time, allow next day
    """
    if not isinstance(time_str, str):
        raise ValueError("时间必须是字符串")
    if base_date is None:
        base_date = date.today()

    time_str = time_str.strip()

    # Handle Chinese relative dates
    chinese_dates = {
        "今天": 0,
        "明天": 1,
        "后天": 2,
        "大后天": 3,
    }

    for cn_date, days_offset in reversed(chinese_dates.items()):
        if time_str.startswith(cn_date):
            time_str = time_str[len(cn_date):].strip()
            target_date = base_date + relativedelta(days=days_offset)
            if time_str:
                parsed_time = _parse_time_only(time_str)
                return datetime.combine(target_date, parsed_time.time())
            return datetime.combine(target_date, datetime.min.time())

    # Handle English relative dates
    relative_patterns = [
        (r"^tomorrow\s+", 1),
        (r"^today\s+", 0),
        (r"^next\s+week\s+", 7),
        (r"^in\s+(\d+)\s+hours?$", None),
        (r"^in\s+(\d+)\s+minutes?$", None),
    ]

    for pattern, days_offset in relative_patterns:
        match = re.match(pattern, time_str, re.IGNORECASE)
        if match:
            if days_offset is not None:
                target_date = base_date + relativedelta(days=days_offset)
                time_part = time_str[match.end():].strip()
                if time_part:
                    parsed_time = _parse_time_only(time_part)
                    return datetime.combine(target_date, parsed_time.time())
                return datetime.combine(target_date, datetime.min.time())
            else:
                value = int(match.group(1))
                if "hour" in time_str.lower():
                    return datetime.now() + relativedelta(hours=value)
                elif "minute" in time_str.lower():
                    return datetime.now() + relativedelta(minutes=value)

    # Time-only strings must be checked BEFORE dateutil, because
    # dateutil.parse("18:00") silently fills in today's date and would
    # make allow_next_day dead code.
    if _is_clock_time(time_str):
        parsed_time = _parse_time_only(time_str)
        result = datetime.combine(base_date, parsed_time.time())

        # If allow_next_day and the time has already passed today,
        # assume next day (for cross-day scenarios like 23:00 -> 05:00)
        if allow_next_day and result < datetime.now():
            result += timedelta(days=1)

        return result

    # Absolute datetime (must contain an explicit date part)
    try:
        return normalize_local_time(date_parser.parse(time_str))
    except (ValueError, OverflowError, OSError):
        raise ValueError(f"无法解析时间: {time_str}")


def _parse_time_only(time_str: str) -> datetime:
    """Parse time-only string like '18:00' or '6pm'."""
    time_str = time_str.strip()

    # Try HH:MM format
    if re.match(r"^\d{1,2}:\d{2}$", time_str):
        return datetime.strptime(time_str, "%H:%M")

    # Try HH:MM:SS format
    if re.match(r"^\d{1,2}:\d{2}:\d{2}$", time_str):
        return datetime.strptime(time_str, "%H:%M:%S")

    # Try 12-hour format
    for fmt in ["%I%p", "%I %p", "%I:%M%p", "%I:%M %p"]:
        try:
            return datetime.strptime(time_str, fmt)
        except ValueError:
            continue

    return date_parser.parse(time_str)


def validate_time_window(start: datetime | None, end: datetime | None) -> None:
    if start is not None and end is not None and end <= start:
        raise ValueError("结束时间必须晚于开始时间")


def parse_cli_time_changes(
    start_text: str | None,
    end_text: str | None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, datetime]:
    """Resolve bare clock inputs while preserving explicitly supplied dates."""
    now = datetime.now()
    start_is_clock = bool(start_text and _is_clock_time(start_text))
    end_is_clock = bool(end_text and _is_clock_time(end_text))
    changes = {}

    if start_text:
        start = parse_time(start_text, base_date=now.date(), allow_next_day=False)
        if start_is_clock and not end_text and end is None and start < now:
            start += timedelta(days=1)
        changes["start_time"] = start

    if end_text:
        base_date = start.date() if end_is_clock and start else now.date()
        end = parse_time(end_text, base_date=base_date, allow_next_day=False)
        if end_is_clock and end < (start or now):
            end += timedelta(days=1)
        if start_is_clock and end_is_clock and end < now:
            changes["start_time"] = start + timedelta(days=1)
            end += timedelta(days=1)
        changes["end_time"] = end

    return changes


def format_datetime(dt: datetime | None, include_date: bool = True) -> str:
    """Format datetime for display."""
    if dt is None:
        return "-"

    today = date.today()
    if dt.date() == today:
        return f"今天 {dt.strftime('%H:%M')}"
    elif dt.date() == today + relativedelta(days=1):
        return f"明天 {dt.strftime('%H:%M')}"
    elif include_date:
        return dt.strftime("%Y-%m-%d %H:%M")
    else:
        return dt.strftime("%H:%M")


def format_time_window(start: datetime | None, end: datetime | None) -> str:
    """Format time window for display, handling cross-day scenarios."""
    if start and end:
        cross_day = end.date() != start.date()
        start_str = format_datetime(start, include_date=cross_day)
        end_str = format_datetime(end) if cross_day else end.strftime("%H:%M")
        return f"{start_str} - {end_str}"
    elif end:
        return f"截止: {format_datetime(end)}"
    elif start:
        return f"开始: {format_datetime(start)}"
    return "-"


def get_relative_time(dt: datetime) -> str:
    """Get relative time description (e.g., '2小时后', '已过期')."""
    if dt is None:
        return ""

    now = datetime.now()
    delta = dt - now

    if delta.total_seconds() < 0:
        return "已过期"

    total_minutes = int(delta.total_seconds() / 60)
    if total_minutes < 60:
        return f"{total_minutes}分钟后"
    elif total_minutes < 24 * 60:
        hours = total_minutes // 60
        minutes = total_minutes % 60
        if minutes > 0:
            return f"{hours}小时{minutes}分钟后"
        return f"{hours}小时后"
    else:
        days = total_minutes // (24 * 60)
        hours = (total_minutes % (24 * 60)) // 60
        if hours > 0:
            return f"{days}天{hours}小时后"
        return f"{days}天后"
