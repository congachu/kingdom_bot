# utils/timezone.py
from datetime import timezone, timedelta
try:
    from zoneinfo import ZoneInfo
    KST = ZoneInfo("Asia/Seoul")
except Exception:
    # tzdata 미설치/OS tzdb 부재 시 안전 폴백
    KST = timezone(timedelta(hours=9))
