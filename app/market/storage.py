"""One compressed, typed Parquet file for the anonymous replay catalog."""

from pathlib import Path
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq

from .data import POLICY_VERSION, content_hash


EVENT = pa.struct([
    ('id', pa.string()), ('location_status', pa.string()), ('period', pa.int64()),
    ('player', pa.string()), ('score', pa.list_(pa.field('element', pa.int64()))), ('sub_player', pa.string()),
    ('team', pa.string()), ('time', pa.float64()), ('type', pa.string()),
    ('type_code', pa.int64()), ('x', pa.float64()), ('y', pa.float64()),
])
SCHEMA = pa.schema([
    ('events', pa.list_(pa.field('element', EVENT))), ('id', pa.string()), ('kickoff', pa.int64()),
    ('label', pa.string()), ('policy_version', pa.string()),
    ('prices', pa.list_(pa.field('element', pa.struct([('p', pa.float64()), ('t', pa.float64())])))),
    ('schema', pa.string()), ('payload_sha256', pa.string()),
], metadata={b'market_catalog': b'v2', b'policy_version': POLICY_VERSION.encode()})


def write_catalog(games, destination):
    """Publish atomically; never replace an existing catalog."""
    destination = Path(destination)
    if destination.suffix != '.parquet':
        raise ValueError('Catalog output must be a .parquet file')
    if destination.exists():
        raise ValueError('Output already contains a catalog; choose a new output file')
    if not games:
        raise ValueError('Catalog requires at least one game')
    rows = [{**game, 'payload_sha256': content_hash(game)} for game in games]
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    # Reject type coercion that would invalidate existing recording hashes.
    for row in table.to_pylist():
        expected = row.pop('payload_sha256')
        if content_hash(row) != expected:
            raise ValueError('Parquet conversion changes the game payload')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.market-', dir=destination.parent) as temporary:
        staged = Path(temporary) / 'catalog.parquet'
        pq.write_table(table, staged, compression='zstd', compression_level=9)
        # Hard linking publishes the completed file without an overwrite race.
        destination.hardlink_to(staged)


def read_catalog(path):
    try:
        table = pq.read_table(path)
        if not table.schema.equals(SCHEMA, check_metadata=True):
            raise ValueError('Unsupported anonymous Parquet catalog schema')
        rows = table.to_pylist()
        if not rows:
            raise ValueError('Catalog requires at least one game')
        for row in rows:
            expected = row.pop('payload_sha256')
            if content_hash(row) != expected:
                raise ValueError('Game content hash mismatch')
        return rows
    except (pa.ArrowException, OSError, TypeError) as exc:
        raise ValueError('Game catalog is missing or malformed') from exc
