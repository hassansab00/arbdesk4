"""Local JSON in, local report out. No environment credentials are read."""
import argparse
import json
from pathlib import Path
import sys

from .core import evaluate, load_json
from .historical import audit_scores


def percent(value):
    return 'unknown' if value is None else f'{value:.1%}'


def markdown(report):
    lines = ['# Independent predictive evaluation', '',
             '**Status: ' + report['readiness']['status'] + '**. No automatic promotion or live changes.', '',
             'Input SHA256: `' + report['input_sha256'] + '`', '',
             '## Readiness reasons', '']
    lines.extend('- ' + r for r in report['readiness']['reasons'])
    if not report['readiness']['reasons']:
        lines.append('The declared offline gates passed. Upstream evidence and independent approval still need review.')
    if report.get('mode') == 'historical_score_audit':
        lines += ['', '## Stored scores by city and checkpoint', '',
                  'These are descriptive calls, not independent daily trials or a validated entry recommendation.', '',
                  '| City | Checkpoint | Calls / target dates | Model | Market on paired calls | At/after high / reviewed calls |',
                  '|---|---|---:|---:|---:|---:|']
        for r in report['by_city_checkpoint']:
            lines.append(f"| {r['city']} | {r['checkpoint']} | {r['calls']} / {r['target_dates']} | {percent(r['model_hit_rate'])} | {percent(r['market_hit_rate'])} | {r['calls_at_or_after_first_max']} / {r['reviewed_calls']} |")
    else:
        lines += ['', '## Paired comparisons', '',
                  'Readiness statistics use the registered primary advance slots; late-slot gains cannot qualify a candidate.', '',
                  '| Baseline | All paired / planned | Primary candidate hit rate | Primary baseline hit rate | Primary candidate Brier | Primary baseline Brier |',
                  '|---|---:|---:|---:|---:|---:|']
        for bid, r in report['comparisons'].items():
            a, b = r['primary_candidate'], r['primary_baseline']
            lines.append(f"| {bid} | {r['paired_cases']} / {r['planned_cases']} | {percent(a['hit_rate'])} | {percent(b['hit_rate'])} | {a['brier']} | {b['brier']} |")
        lines += ['', 'The JSON contains date-block evidence, city breakdowns, lead groups, reliability bins and exclusion reasons.']
    lines += ['', '## Limits', ''] + ['- ' + r for r in report['limitations']]
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', help='New directory only; existing data is never overwritten.')
    parser.add_argument('--require-ready', action='store_true', help='Exit 2 when held/not assessable, after writing the report.')
    commands = parser.add_subparsers(dest='command', required=True)
    strict = commands.add_parser('evaluate', help='Validate a registered shared-candidate export.')
    strict.add_argument('input')
    history = commands.add_parser('audit-scores', help='Describe stored score extracts; never approve a model.')
    history.add_argument('input')
    history.add_argument('--cities', required=True, help='City/timezone JSON export.')
    history.add_argument('--peak-reviews', required=True, help='Station review JSON export, [] allowed.')
    history.add_argument('--as-of', required=True, help='Offset-aware outcome availability cutoff.')
    history.add_argument('--city-cohort', help='Explicit comma-separated city keys; omit to describe all supplied cities.')
    args = parser.parse_args(argv)
    try:
        packet = load_json(args.input)
        if args.command == 'evaluate':
            report = evaluate(packet)
        else:
            report = audit_scores(packet, load_json(args.cities), load_json(args.peak_reviews), args.as_of,
                                  set(args.city_cohort.split(',')) if args.city_cohort else None)
        body = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + '\n'
        prose = markdown(report)
        if args.output_dir:
            directory = Path(args.output_dir)
            directory.mkdir(parents=True, exist_ok=False)
            (directory / 'report.json').write_text(body, encoding='utf-8')
            (directory / 'report.md').write_text(prose, encoding='utf-8')
            print(str(directory.resolve() / 'report.md'))
        else:
            print(body, end='')
        return 2 if args.require_ready and report['readiness']['status'] != 'ready_for_independent_review' else 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f'evaluation failed: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
