"""Docker-only read of all exact public Parquet metadata, no row reads."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import struct


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('metadata', type=Path)
    parser.add_argument('--state-sha256', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if not Path('/.dockerenv').exists():
        raise SystemExit('Docker only')
    import pyarrow
    import pyarrow.parquet as pq
    state_raw = (args.metadata / 'state.json').read_bytes()
    assert hashlib.sha256(state_raw).hexdigest() == args.state_sha256
    state = json.loads(state_raw)
    assert state['status'] == 'COMPLETE16_PART_FOOTERS_AND_RELEASE_METADATA_ACQUIRED_SCHEMA_PARSE_PENDING'
    assert len(state['parts']) == 16 and len(state['requests']) == 41
    records = {request['name']: request for request in state['requests']}
    schemas, parts, total, findings = [], [], 0, []
    for part in state['parts']:
        key = part['evidence_key']
        footer_name, trailer_name = key + '-footer.bin', key + '-trailer.bin'
        footer, trailer = (args.metadata / footer_name).read_bytes(), (args.metadata / trailer_name).read_bytes()
        for name, raw in ((footer_name, footer), (trailer_name, trailer)):
            assert hashlib.sha256(raw).hexdigest() == records[name]['sha256']
            assert len(raw) == records[name]['received_bytes']
            assert records[name]['status'] == 206
        assert len(trailer) == 8 and trailer[-4:] == b'PAR1'
        assert struct.unpack('<I', trailer[:4])[0] == len(footer) <= part['bytes'] - 12
        # Only metadata is read; original data-page offsets are never dereferenced.
        metadata = pq.read_metadata(io.BytesIO(b'PAR1' + footer + trailer))
        kv = metadata.metadata or {}
        key_name = b'org.apache.spark.sql.parquet.row.metadata'
        if key_name in kv:
            spark_schema = json.loads(kv[key_name])
        else:
            spark_schema = None
            findings.append({'part': part['name'], 'finding': 'Spark schema key absent; physical schema is not a substitute'})
        schemas.append(spark_schema)
        total += metadata.num_rows
        parts.append({'name': part['name'], 'etag': part['etag'], 'num_rows': metadata.num_rows,
                      'spark_schema': spark_schema, 'created_by': metadata.created_by,
                      'num_row_groups': metadata.num_row_groups,
                      'scope': 'schema-only; data values not read',
                      'physical_schema_crosscheck': str(metadata.schema),
                      'arrow_schema_crosscheck': str(metadata.schema.to_arrow_schema())})
    if not all(schema == schemas[0] for schema in schemas):
        findings.append({'finding': 'Part Spark schemas differ'})
    numeric = ['age', 'education-num', 'capital-gain', 'capital-loss', 'hours-per-week']
    by_name = {field['name']: field['type'] for field in schemas[0]['fields']} if schemas[0] else {}
    if not all(name in by_name for name in numeric):
        findings.append({'finding': 'Numeric feature fields missing from Spark metadata'})
    args.out.write_text(json.dumps({'status': 'SCHEMA_FINDINGS' if findings else 'ALL16_SPARK_SCHEMAS_VERIFIED',
        'findings': findings, 'pyarrow': pyarrow.__version__,
        'numeric_feature_types': {name: by_name.get(name) for name in numeric}, 'parts': parts,
        'row_count_from_complete_metadata': total, 'notebook_prose_rows': 32561,
        'matches_notebook_prose': total == 32561, 'data_values_read_or_verified': False,
        'metadata_state_sha256': args.state_sha256}, indent=2) + '\n')

if __name__ == '__main__':
    main()
