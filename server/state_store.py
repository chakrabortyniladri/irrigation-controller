"""state.json: schedule, ESP GUID, last scheduled run. Survives power cuts.

save() writes a temp file, fsyncs it, renames it over the old one and fsyncs the
directory, so a cut leaves either the old or the new file, never a torn one.
"""
import json
import logging
import os
import tempfile

from schedule import DEFAULT_SCHEDULE, validate_schedule

log = logging.getLogger(__name__)


def defaults():
    return {'schedule': dict(DEFAULT_SCHEDULE), 'guid': None, 'last_run': None}


def load(path):
    try:
        with open(path) as f:
            data = json.load(f)
        data['schedule'] = validate_schedule(data['schedule'])
    except FileNotFoundError:
        return defaults()
    except (ValueError, KeyError, TypeError) as e:     # JSONDecodeError is a ValueError
        log.warning('state file %s unusable (%s), using defaults', path, e)
        return defaults()
    out = defaults()
    out.update({k: data.get(k, v) for k, v in out.items()})
    return out


def save(path, data):
    d = os.path.dirname(path) or '.'
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.state-')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    dfd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(dfd)          # make the rename itself durable
    finally:
        os.close(dfd)
