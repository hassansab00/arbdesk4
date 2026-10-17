"""Insert immutable calls without one expired city discarding the batch."""
import time


def freeze_insert(table, rows, conflict, *, writer, refused, deadline=None):
    def is_guard(exc):
        return 'Freeze refused:' in str(exc) or 'Freeze refused:' in str(getattr(getattr(exc,'response',None),'text',''))
    try:
        return writer(table, rows, conflict)
    except Exception as exc:
        if not is_guard(exc):
            raise
    written=0
    for row in rows:
        if deadline is not None and time.monotonic()>=deadline:
            refused.append('retry deadline reached; remaining rows deferred')
            break
        try:
            written+=writer(table,[row],conflict)
        except Exception as exc:
            if not is_guard(exc):
                raise
            refused.append(f"{row['city_key']} {row['target_date']} {row['checkpoint']}: Freeze refused")
    return written
