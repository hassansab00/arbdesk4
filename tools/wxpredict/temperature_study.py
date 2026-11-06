"""Private chronological development and fixed forward fitting. No DB writes."""
import argparse
import collections
import datetime as dt
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wxpredict import temperature_model as model, temperature_evaluation as evaluation, pre_day_pairs as pp


def run(input_path, output_path, validation_first, cutoff):
    root = Path(__file__).resolve().parents[2]
    out = Path(output_path).resolve()
    if out.exists() or out == root or out.is_relative_to(root):
        raise ValueError('use a new private output directory outside the repository')
    records = json.loads(Path(input_path).read_text())
    if pp.timestamp(cutoff) > dt.datetime.now(dt.timezone.utc):
        raise ValueError('future study cutoff')
    counts = collections.Counter()
    accepted = []
    for r in records:
        try:
            model.validate(r)
        except (KeyError, ValueError, TypeError) as error:
            counts[str(error)] += 1
            continue
        if not r.get('label') or pp.timestamp(r['label']['available_at']) >= pp.timestamp(cutoff):
            counts['label_unavailable_before_cutoff'] += 1
            continue
        accepted.append(r)
    test = [r for r in accepted if r['date'] >= validation_first]
    if not test:
        raise ValueError('no chronological development dates')
    boundary = min(pp.timestamp(r['as_of']) for r in test).isoformat()
    train = [r for r in accepted if r['date'] < validation_first and pp.timestamp(r['label']['available_at']) < pp.timestamp(boundary)]
    development = model.fit(train, boundary)
    by_slot = {}
    predictions = [model.predict(development, r) for r in test]
    for slot in model.SLOTS:
        rows = [evaluation.metrics(p['probs'], r['label']['winner']) for r, p in zip(test, predictions) if r['checkpoint'] == slot]
        by_slot[slot] = dict(calls=len(rows), dates=len({r['date'] for r in test if r['checkpoint'] == slot}),
                             **{k: sum(r[k] for r in rows)/len(rows) if rows else None for k in ('hit', 'log_loss', 'brier')})
    forward = model.fit(accepted, cutoff)
    report = dict(policy=model.POLICY, interpretation='Previously inspected development dates, never a forward freeze or promotion test',
                  changes_serving=False, exclusions=dict(counts), development_training=development['training'],
                  development=by_slot, forward_training=forward['training'], forward_artifact_sha256=forward['sha256'])
    out.mkdir(parents=True)
    for name, value in [('development_artifact.json', development), ('forward_artifact.json', forward), ('report.json', report)]:
        (out/name).write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False)+'\n')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--validation-first', required=True)
    p.add_argument('--cutoff', required=True)
    args = p.parse_args()
    print(json.dumps(run(args.input, args.out, args.validation_first, args.cutoff), sort_keys=True))
