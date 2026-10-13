# Data

`manifests/sports/` contains frozen sample selections and source provenance. `processed/sports/` contains the original audit tables, normalized FIFA events and two-match market join.

Downloaded provider payloads belong under `raw/sports/`, which is ignored by Git. Recreate them with the [pipelines](../docs/pipelines/README.md). The committed results are an audit snapshot, not a guarantee that future provider responses remain identical.

Small examples needed for an offline application demo may be committed under `examples/` with source metadata. Keep large caches and machine-local files out of commits.

The shared FIFA ingestion pipeline writes ignored, local Parquet snapshots under `processed/fifa/` and verified raw payloads under `raw/fifa/`. See the [pipeline guide](../docs/pipelines/fifa-parquet.md). The two Parquet tables support both Premier League and World Cup records.
