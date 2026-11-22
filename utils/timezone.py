from datetime import datetime, timedelta, timezone


MALAYSIA_TZ = timezone(timedelta(hours=8), name="MYT")

DEFAULT_FORMAT = "%d %b %Y, %H:%M"


# Convert a UTC datetime to Malaysia time (UTC+8).
def to_malaysia_time(value: datetime | None):

    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(MALAYSIA_TZ)


# Jinja filter 'myt': format a UTC datetime in Malaysia time.
def format_malaysia_time(value: datetime | None, fmt: str = DEFAULT_FORMAT):

    local_time = to_malaysia_time(value)

    if local_time is None:
        return ""

    return local_time.strftime(fmt)
