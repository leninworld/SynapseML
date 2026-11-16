"""Hash-bound standard upstream test fixtures, in owned hosted Docker only."""
import json
import sys
import time
from pathlib import Path

from bootstrap import download
from validation_runtime import docker_only, extract_archive, require


def main():
    docker_only()
    packet, runtime, deadline = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3])
    require(time.time() < deadline - 240, 'No fixture and validation reserve')
    manifest = json.loads((packet / 'manifest.json').read_text())
    item = manifest['test_fixtures']
    archive = runtime / 'standard-upstream-test-fixtures.tgz'
    receipt = download(item, archive, deadline)
    require(receipt['bytes'] == item['expected_bytes'], 'Fixture byte count mismatch')
    destination = runtime / 'test-fixtures-extracted'
    records = extract_archive(archive, destination, item['expanded_byte_cap'], item['member_cap'])
    home = destination / 'datasets-2023-04-03'
    require(home.is_dir() and not home.is_symlink(), 'Actual standard fixture root missing')
    result = {'complete': True, 'archive': receipt, 'extracted_files': len(records),
              'expanded_bytes': sum(record['bytes'] for record in records),
              'dataset_dir': str(home), 'members': records,
              'source_binding': 'ThisBuild / datasetDir runtime setting, not environment alone',
              'no_data_redistribution': True}
    (runtime / 'standard-test-fixtures.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({key: value for key, value in result.items() if key != 'members'}))


if __name__ == '__main__':
    main()
