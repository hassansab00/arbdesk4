"""Bank confirmed ladders after the hourly confirmation collector.

The checkpoint bank alone does not populate the day-ahead hit history.
Reuse the existing immutable, whole-ladder bank, then durably request its
page refresh. This runs outside the confirmation sweep's deadline.
"""
import datetime as dt
import uuid
from types import SimpleNamespace

from common import rest_all, rpc, log_run
from databank import bank_bands_only

JOB = "settlement_followup"
DAYS_BACK = 3


def main(today=None):
    today = today or dt.datetime.now(dt.timezone.utc).date()
    try:
        pending = rest_all("v_outcome_pipeline", [
            ("select", "market_id"),
            # The complete-proof attempt can precede the periodic market
            # flag refresh. The bank itself verifies the whole proof ladder.
            ("state", "in.(confirmed_not_banked,venue_resolved_proof_incomplete)"),
            ("resolution_date", f"gte.{today - dt.timedelta(days=DAYS_BACK)}"),
            ("resolution_date", f"lt.{today}"),
        ], order="market_id.asc", page_size=1000)
        if not pending:
            detail = {"pending": 0, "bands": 0, "refresh_requested": False}
        else:
            banked = bank_bands_only(SimpleNamespace(
                days=DAYS_BACK, proof_days=DAYS_BACK, force=False))
            detail = {**banked, "pending": len(pending), "refresh_requested": False}
            if banked["bands"]:
                request_id = str(uuid.uuid4())
                receipt = rpc("request_page_cache_refresh", {"p_request_id": request_id}, timeout=15)
                if receipt.get("status") not in ("queued", "running", "waiting", "ok"):
                    raise RuntimeError(f"Page refresh {request_id} was not accepted: {receipt.get('status')}")
                # Acceptance is not completion; the durable request keeps its
                # own completion/error receipt in ingest_log.
                detail.update(refresh_requested=True, refresh_request_id=request_id,
                              refresh_status=receipt["status"])
        log_run(JOB, "ok", detail["bands"], detail)
        print(detail)
        return detail
    except Exception as exc:
        log_run(JOB, "error", 0, {"error": str(exc)[:300]})
        raise


if __name__ == "__main__":
    main()
