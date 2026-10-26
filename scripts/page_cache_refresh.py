"""Submit a durable cache refresh, optionally waiting for completion.

The gateway can abandon a synchronous refresh before the client's deadline.
A durable request ID survives an ambiguous submission; retries reuse it.
Errors and an unacknowledged deadline remain failures, never synthetic success.
"""
import time
import uuid
import requests


def _call(name, request_id, deadline, rpc_fn):
    for attempt in range(3):
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"page cache refresh {request_id}: completion deadline reached")
        try:
            return rpc_fn(name, {'p_request_id':request_id}, timeout=min(15,remaining))
        except requests.RequestException as exc:
            response = getattr(exc,'response',None)
            status = response.status_code if response is not None else None
            if attempt==2 or status is not None and status not in (408,429,500,502,503,504):
                raise RuntimeError(f"page cache refresh {request_id}: transport failed: {exc}") from exc
            time.sleep(min(2**attempt,max(0,deadline-time.monotonic())))


def request(timeout=45, *, request_id=None, rpc_fn=None):
    """Return a durable acceptance receipt, without claiming cache completion.

    For a pipeline's final display-only step: the existing database worker
    finishes independently and records terminal success/error for the watchdog.
    Transport failures and unacknowledged submissions still fail the caller.
    """
    if rpc_fn is None:
        from common import rpc as rpc_fn
    request_id = request_id or str(uuid.uuid4())
    receipt = _call('request_page_cache_refresh', request_id,
                    time.monotonic()+timeout, rpc_fn)
    if not isinstance(receipt, dict) or receipt.get('request_id') != request_id:
        raise RuntimeError(f"page cache refresh {request_id}: invalid acceptance receipt")
    state = receipt.get('status')
    if state not in ('queued','running','waiting','ok'):
        raise RuntimeError(f"page cache refresh {request_id}: {state}: {receipt.get('detail')}")
    if state == 'ok':
        result = (receipt.get('detail') or {}).get('ms')
        if not isinstance(result, dict) or 'skipped' in result:
            raise RuntimeError(f"page cache refresh {request_id}: invalid completion receipt")
    return receipt


def refresh(timeout=270, *, request_id=None, rpc_fn=None):
    if rpc_fn is None:
        from common import rpc as rpc_fn
    request_id = request_id or str(uuid.uuid4())
    deadline = time.monotonic()+timeout
    def call(name):
        return _call(name, request_id, deadline, rpc_fn)
    receipt = call('request_page_cache_refresh')
    final_read = False
    while True:
        state = receipt.get('status')
        if state == 'ok':
            result = (receipt.get('detail') or {}).get('ms')
            if not isinstance(result, dict) or 'skipped' in result:
                raise RuntimeError(f"page cache refresh {request_id}: invalid completion receipt")
            return result
        if state in ('error','missing'):
            raise RuntimeError(f"page cache refresh {request_id}: {state}: {receipt.get('detail')}")
        if state not in ('queued','running','waiting'):
            raise RuntimeError(f"page cache refresh {request_id}: unknown state {state}")
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            if not final_read:
                # A status request can hold an older transaction snapshot
                # while the worker commits its receipt. Read once more at
                # the boundary before declaring an acknowledged job lost.
                # Read only: never submit a second refresh.
                final_read = True
                receipt = rpc_fn('page_cache_refresh_status',
                                 {'p_request_id': request_id}, timeout=15)
                continue
            raise TimeoutError(f"page cache refresh {request_id} has no completion receipt within {timeout}s; check the request before retrying")
        time.sleep(min(5,remaining))
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            continue
        receipt = call('page_cache_refresh_status')
