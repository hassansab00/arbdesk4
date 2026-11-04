"""Register a fixed private temperature candidate, or an explicit unfitted reference."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from wxpredict import temperature_model as model, temperature_evaluation as evaluation


def register(artifact, cohort, rpc):
    model.model(artifact)
    if not isinstance(cohort, list) or not 20 <= len(set(cohort)) == len(cohort) <= 64:
        raise ValueError('fixed unique temperature cohort required')
    receipt = rpc('temperature_register', dict(p_artifact=artifact, p_cohort=cohort, p_protocol=evaluation.specification()), timeout=8)
    returned = rpc('temperature_inputs', timeout=8)
    model.model(returned['artifact'])
    if (returned['artifact'] != artifact or returned['registered_at'] != receipt['registered_at']
            or returned['cohort'] != cohort or returned['protocol'] != evaluation.specification()
            or receipt['model_sha256'] != artifact['sha256']):
        raise ValueError('temperature registration roundtrip mismatch')
    return dict(receipt, roundtrip_verified=True, changes_serving=False)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--artifact', type=Path, required=True)
    p.add_argument('--cohort', type=Path, required=True)
    args = p.parse_args()
    import common
    print(json.dumps(register(json.loads(args.artifact.read_text()), json.loads(args.cohort.read_text()), common.rpc), sort_keys=True))
