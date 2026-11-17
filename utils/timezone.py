# ============================================================
# Timezone Utilities
# 数据库统一保存 UTC 时间；显示给用户时才换算成马来西亚时间
# ============================================================

from datetime import datetime, timedelta, timezone


# 马来西亚时间：UTC+8
# 马来西亚没有夏令时，所以固定 +8 小时即可
MALAYSIA_TZ = timezone(timedelta(hours=8), name="MYT")

# 本项目最常用的显示格式，例如 "16 Nov 2026, 10:44"
DEFAULT_FORMAT = "%d %b %Y, %H:%M"


# ============================================================
# 把 UTC 时间换算成马来西亚时间
# Parameter：value，datetime（数据库读出的 UTC 时间）
# Return：带有马来西亚时区的 datetime；value 为 None 时返回 None
# ============================================================
def to_malaysia_time(value: datetime | None):

    if value is None:
        return None

    # 数据库存的是"没有时区信息"的 UTC 时间（见 models.py 的 utc_now()）
    # 先明确标记它是 UTC，Python 才知道要从哪个时区换算
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    # astimezone()：换算到马来西亚时区（UTC 10:00 → MYT 18:00）
    return value.astimezone(MALAYSIA_TZ)


# ============================================================
# 换算成马来西亚时间，并格式化成字符串
# 注册为 Jinja2 Filter 后，在 Template 中写：
#   {{ claim.created_at|myt }}
#   {{ log.created_at|myt("%Y-%m-%d %H:%M:%S") }}
# ============================================================
def format_malaysia_time(value: datetime | None, fmt: str = DEFAULT_FORMAT):

    local_time = to_malaysia_time(value)

    # 没有时间时显示空字符串，避免页面出现 "None"
    if local_time is None:
        return ""

    return local_time.strftime(fmt)
