"""Regrade saved intent observations uniformly; never run a model or overwrite raw data."""

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.benchmark_intent import DATASET, score, summarize


def evaluate(path):
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    metadata = rows[0]
    if metadata['dataset_sha256'] != sha256(DATASET.read_bytes()).hexdigest():
        raise ValueError('Run does not match the frozen dataset.')
    cases = {c['id']: c for c in json.loads(DATASET.read_bytes())['cases']}
    records, seen = [], set()
    for raw in rows[1:]:
        if raw['kind'] != 'observation':
            continue
        key = (raw['trial'], raw['case'], raw['turn'])
        if key in seen:
            raise ValueError('Duplicate saved observation.')
        seen.add(key)
        turn = cases[raw['case']]['turns'][raw['turn']]
        record = deepcopy(raw)
        record['original_scores'] = record['scores']
        record['scores'] = score(turn, record)
        records.append(record)
    expected = {(trial, name, turn) for trial in range(metadata['trials'])
                for name in metadata['cases'] for turn in range(len(cases[name]['turns']))}
    if seen != expected:
        raise ValueError('Saved run incomplete; report missing observations explicitly.')
    return {'raw_path': str(path), 'raw_sha256': sha256(path.read_bytes()).hexdigest(),
            'metadata': metadata, 'summary': summarize(records), 'records': records}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', nargs='+', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    result = {'grading': 'v1.1 (also recognizes imperative Please clarify replies)',
              'dataset_sha256': sha256(DATASET.read_bytes()).hexdigest(),
              'grader_sha256': sha256(Path(__file__).with_name('benchmark_intent.py').read_bytes()).hexdigest(),
              'runs': [evaluate(path) for path in args.runs]}
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write('\n')
    for run in result['runs']:
        print(run['raw_path'], json.dumps(run['summary']['overall']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
