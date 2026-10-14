"""Schedule rule shared with the ESP firmware. Pure: no I/O, testable off-Pi.

Run if enabled, clock synced, start <= now < 22:00 (same local day) and the ESP hasn't
recorded a scheduled run today. That one rule covers on-time runs and power-cut catch-up.
"""
import re

MAX_RUN_S = 90                 # more than this overflows; the ESP clamps to the same value
DEFAULT_RUN_S = 60
CUTOFF = (22, 0)               # missed runs are dropped from 22:00
DEFAULT_SCHEDULE = {'enabled': False, 'time': '08:00', 'seconds': DEFAULT_RUN_S}
_TIME = re.compile(r'([01]\d|2[0-3]):[0-5]\d')


def day_key(dt):
    """Same value the ESP stores: year*1000 + C tm_yday (0-based; Python's is 1-based)."""
    return dt.year * 1000 + dt.timetuple().tm_yday - 1


def _hm(text):
    return int(text[:2]), int(text[3:])


def due(sched, now, synced, esp_last_day):
    if not sched['enabled'] or not synced or esp_last_day is None:
        return False
    return (_hm(sched['time']) <= (now.hour, now.minute) < CUTOFF
            and esp_last_day != day_key(now))


def next_run(sched, now, ran_today):
    if not sched['enabled']:
        return 'Schedule off'
    t = sched['time']
    if ran_today or (now.hour, now.minute) >= CUTOFF:
        return f'tomorrow {t}'
    if (now.hour, now.minute) < _hm(t):
        return f'today {t}'
    return 'now (catching up)'


def validate_seconds(v):
    # type() not isinstance(): bool is an int subclass and True must not mean 1 s
    if type(v) is not int or not 1 <= v <= MAX_RUN_S:
        raise ValueError(f'seconds must be a whole number 1..{MAX_RUN_S}')
    return v


def validate_schedule(d):
    if not isinstance(d, dict) or type(d.get('enabled')) is not bool:
        raise ValueError('enabled must be true or false')
    t = d.get('time')
    if not isinstance(t, str) or not _TIME.fullmatch(t):
        raise ValueError('time must be HH:MM')
    if _hm(t) >= CUTOFF:
        raise ValueError('start time must be before 22:00')
    return {'enabled': d['enabled'], 'time': t, 'seconds': validate_seconds(d.get('seconds'))}
