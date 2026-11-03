"""Register a private fitted candidate and verify the database round trip.

The artifact stays outside the repository. Registration never changes the
production predictor and cannot turn historical predictions into forward calls.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from wxpredict import shared_weather as model


def register(artifact, cohort, rpc):
    model.model(artifact)
    if (not isinstance(cohort, list) or not 20 <= len(cohort) <= 64
            or any(not isinstance(city, str) or not city for city in cohort)
            or len(set(cohort)) != len(cohort)):
        raise ValueError('a fixed unique city cohort is required')
    receipt = rpc('shared_weather_register', dict(p_artifact=artifact, p_cohort=cohort), timeout=10)
    received = rpc('shared_weather_inputs', timeout=10)
    # JSON numeric types can compare equal while producing a different digest
    # (2.0 versus 2). Validate the received artifact, not just Python equality.
    model.model(received.get('artifact') or {})
    if (received.get('state') != 'registered'
            or received['artifact'] != artifact
            or receipt.get('model_sha256') != artifact['sha256']
            or received.get('registered_at') != receipt.get('registered_at')
            or received.get('cohort') != cohort
            or receipt.get('cohort') != cohort):
        raise ValueError('registered candidate receipt does not match the database reader')
    return dict(receipt, roundtrip_verified=True, changes_serving=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact', required=True, type=Path)
    parser.add_argument('--cohort', required=True, type=Path)
    args = parser.parse_args()
    import common
    print(json.dumps(register(json.loads(args.artifact.read_text()),
        json.loads(args.cohort.read_text()), common.rpc), sort_keys=True))
