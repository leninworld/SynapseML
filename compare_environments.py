"""Collector-side equivalence check for independently resolved validation environments."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def environment_key(record):
    urls = record['conda_urls']
    distributions = record['python_distributions']
    if not urls or not distributions or not all(isinstance(v,str) and v.startswith('https://') for v in urls):
        raise ValueError('Missing actual resolved environment closure')
    python = []
    for distribution in distributions:
        name = re.sub(r'[-_.]+','-',distribution['name']).lower()
        version = distribution['version']
        if not name or not isinstance(version,str) or not version:
            raise ValueError('Invalid resolved distribution')
        direct = json.dumps(distribution.get('direct_url'),sort_keys=True,separators=(',',':'))
        python.append((name,version,direct))
    # Keep the entire explicit URL, including its hash fragment when provided.
    # RECORD hashes remain evidence, not an equality gate for local build bytes.
    return {'conda_urls':sorted(urls),'python_distributions':sorted(python)}


def compare(baseline, candidate):
    left, right = environment_key(baseline),environment_key(candidate)
    digest = lambda value: hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
    return {'equal':left == right,'baseline_key_sha256':digest(left),
            'candidate_key_sha256':digest(right),'conda_packages':len(left['conda_urls']),
            'python_distributions':len(left['python_distributions']),
            'equality_key':'Entire explicit Conda URLs including provided hashes; normalized name, version and direct_url for Python distributions',
            'record_hashes':'Retained in original closure evidence; informational, not gating'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('baseline',type=Path)
    parser.add_argument('candidate',type=Path)
    parser.add_argument('result',type=Path)
    args = parser.parse_args()
    result = compare(json.loads(args.baseline.read_text()),json.loads(args.candidate.read_text()))
    with args.result.open('x') as output:
        json.dump(result,output,indent=2)
    print(json.dumps(result))
    if not result['equal']:
        raise SystemExit(1)

if __name__ == '__main__':
    main()
