"""Utility functions for time parsing and formatting."""

from datetime import datetime, date, timedelta
from dateutil import parser as date_parser
from dateutil.relativedelta import relativedelta
import re


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

    for cn_date, days_offset in chinese_dates.items():
        if cn_date in time_str:
            time_str = time_str.replace(cn_date, "").strip()
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
    if re.match(r"^\d{1,2}(:\d{2}){1,2}\s*([aApP][mM])?$", time_str) or \
       re.match(r"^\d{1,2}\s*[aApP][mM]$", time_str):
        parsed_time = _parse_time_only(time_str)
        result = datetime.combine(base_date, parsed_time.time())

        # If allow_next_day and the time has already passed today,
        # assume next day (for cross-day scenarios like 23:00 -> 05:00)
        if allow_next_day and result < datetime.now():
            result += timedelta(days=1)

        return result

    # Absolute datetime (must contain an explicit date part)
    try:
        return date_parser.parse(time_str)
    except (ValueError, OverflowError):
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


def validate_time_window(start: datetime | None, end: datetime | None) -> tuple[datetime | None, datetime | None, str | None]:
    """
    Validate and adjust time window.

    Handles cross-day scenarios:
    - If end < start (e.g., 23:00 - 05:00), end is moved to next day
    - If start is in the past but end is in the future, start is moved to next day

    Returns:
        (adjusted_start, adjusted_end, error_message)
    """
    if start is None and end is None:
        return None, None, None

    now = datetime.now()

    # If only end time is set
    if start is None and end is not None:
        # If end time has passed, assume next day
        if end < now:
            end += timedelta(days=1)
        return None, end, None

    # If only start time is set
    if start is not None and end is None:
        if start < now:
            start += timedelta(days=1)
        return start, None, None

    # Both start and end are set
    if start is not None and end is not None:
        # Case 1: end time is earlier than start time (cross-day scenario)
        # e.g., 23:00 today - 05:00 tomorrow
        if end.time() < start.time():
            # End should be next day
            if start.date() == end.date():
                end += timedelta(days=1)

        # Case 2: both times are in the past
        if end < now and start < now:
            # Move both to next day
            start += timedelta(days=1)
            end += timedelta(days=1)

        # Validate: end must be after start
        if end <= start:
            return start, end, f"结束时间 ({format_datetime(end)}) 必须晚于开始时间 ({format_datetime(start)})"

        return start, end, None

    return start, end, None


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
        start_str = format_datetime(start, include_date=False)
        # If end is on a different day, include date info
        if end.date() > start.date():
            days_diff = (end.date() - start.date()).days
            if days_diff == 1:
                end_str = f"明天 {end.strftime('%H:%M')}"
            else:
                end_str = end.strftime("%m-%d %H:%M")
        else:
            end_str = end.strftime("%H:%M")
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
