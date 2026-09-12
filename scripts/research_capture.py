"""Persist predictive/synthesis outputs at computation time, before outcomes.

Capture server-side views with a single transaction and keep original source
records via triggers. Repeated command IDs cannot create duplicate captures.
"""
import os
import uuid
from common import rpc, log_run


def main():
    command = os.environ.get('RESEARCH_CAPTURE_ID') or str(uuid.uuid4())
    version = os.environ.get('GITHUB_SHA') or os.environ.get('ARBDESK_ENGINE_VERSION')
    if not version:
        raise ValueError('Set ARBDESK_ENGINE_VERSION to the deployed commit SHA')
    result = rpc('capture_research_state', {'p_command': command, 'p_engine_version': version}, timeout=120)
    log_run('research_capture', 'ok', result.get('rows', 0), result)
    print(result)


if __name__ == '__main__':
    main()
