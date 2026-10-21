"""SQL functions registered by askdata query: REGEXP, regexp_extract, tz.

tz(ts[, zone]) converts an ISO-8601 timestamp with a zone (Z or +-HH:MM) to
local 'YYYY-MM-DD HH:MM:SS' (default zone America/Chicago, DEC-427), so that
SQLite date()/strftime() work on the result.

- Naive timestamps (no zone) are returned unchanged: they are taken to be
  local already (for example governance_backup.log).
- A date-only value is returned unchanged.
- Anything else returns NULL and is counted in a warning.

Windows has no tz database unless the tzdata package is installed. When
ZoneInfo("America/Chicago") is unavailable, a built-in US Central rule is
used: UTC-6, and UTC-5 from the second Sunday of March 02:00 local to the
first Sunday of November 02:00 local. It is valid from 2007 on; earlier
values return NULL and are counted in a warning.
"""
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_ZONE = "America/Chicago"

_TS = re.compile(
    r"(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?"
    r"\s*(Z|[+-]\d{2}:?\d{2})?\Z", re.IGNORECASE)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


def _nth_sunday(year, month, n):
    first = datetime(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def central_fallback(utc_dt):
    """US Central local time (naive) for an aware UTC datetime, 2007 rules.

    Returns None when the local year is before 2007.
    """
    u = utc_dt.astimezone(timezone.utc).replace(tzinfo=None)
    if (u - timedelta(hours=6)).year < 2007:
        return None
    year = u.year
    # 02:00 CST = 08:00 UTC; 02:00 CDT = 07:00 UTC.
    dst_start = _nth_sunday(year, 3, 2) + timedelta(hours=8)
    dst_end = _nth_sunday(year, 11, 1) + timedelta(hours=7)
    offset = -5 if dst_start <= u < dst_end else -6
    return u + timedelta(hours=offset)


class SqlFunctions:
    """Per-query function state: compiled patterns, zones, warning counts."""

    def __init__(self):
        self.counts = Counter()
        self._patterns = {}
        self._zones = {}

    def _pattern(self, pattern):
        compiled = self._patterns.get(pattern)
        if compiled is None:
            compiled = re.compile(pattern)
            self._patterns[pattern] = compiled
        return compiled

    def regexp(self, pattern, text):
        """X REGEXP Y calls regexp(Y, X)."""
        if pattern is None or text is None:
            return None
        return 1 if self._pattern(str(pattern)).search(str(text)) else 0

    def regexp_extract(self, text, pattern, group=0):
        if text is None or pattern is None or group is None:
            return None
        m = self._pattern(str(pattern)).search(str(text))
        if m is None:
            return None
        return m.group(int(group))

    def _zone(self, name):
        if name not in self._zones:
            try:
                self._zones[name] = ZoneInfo(name)
            except ZoneInfoNotFoundError:
                if name != DEFAULT_ZONE:
                    raise
                self._zones[name] = None  # use central_fallback
        return self._zones[name]

    def tz(self, ts, zone=None):
        if ts is None:
            return None
        text = str(ts).strip()
        if _DATE.match(text):
            return text
        m = _TS.match(text)
        if m is None:
            self.counts["tz_unparsable"] += 1
            return None
        day, hh, mm, ss, offset = m.groups()
        if offset is None:
            return text
        if offset.upper() == "Z":
            offset = "+00:00"
        elif ":" not in offset:
            offset = offset[:3] + ":" + offset[3:]
        try:
            dt = datetime.fromisoformat(f"{day}T{hh}:{mm}:{ss or '00'}{offset}")
        except ValueError:
            self.counts["tz_unparsable"] += 1
            return None
        name = DEFAULT_ZONE if zone is None else str(zone)
        tzinfo = self._zone(name)
        if tzinfo is None:
            local = central_fallback(dt)
            if local is None:
                self.counts["tz_before_2007"] += 1
                return None
        else:
            local = dt.astimezone(tzinfo).replace(tzinfo=None)
        return local.strftime("%Y-%m-%d %H:%M:%S")

    def register(self, conn):
        conn.create_function("regexp", 2, self.regexp, deterministic=True)
        conn.create_function("regexp_extract", 2, self.regexp_extract, deterministic=True)
        conn.create_function("regexp_extract", 3, self.regexp_extract, deterministic=True)
        # tz counts warnings, so it is not declared deterministic.
        conn.create_function("tz", 1, self.tz)
        conn.create_function("tz", 2, self.tz)

    def warnings(self):
        result = []
        if self.counts["tz_unparsable"]:
            result.append(f"tz: {self.counts['tz_unparsable']} values not recognised "
                          f"as timestamps, returned NULL")
        if self.counts["tz_before_2007"]:
            result.append(f"tz: {self.counts['tz_before_2007']} values before 2007 with the "
                          f"built-in US Central rule (no tz database), returned NULL")
        return result
