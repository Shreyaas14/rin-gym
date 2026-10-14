# Market demo validation

Small verification summaries are versioned here. Complete browser journals,
live-model response captures, and the full generated game ranking stay local
and are ignored by Git. The PR's simulation GIF is a GitHub attachment.

To capture a new journal, use **Export session** in the replay UI, then audit it:

```sh
uv run --locked python -m app.market.audit /absolute/path/to/session.json
```

To reproduce the complete anonymous ranking from the committed Parquet file:

```sh
uv run --locked python - <<'PY'
import json
from pathlib import Path
from app.market.catalog import Catalog

output = Path('artifacts/market-demo/anonymous-catalog/event-density-ranking.json')
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(Catalog().ranking_report(), indent=2) + '\n')
PY
```

The dataset in `data/processed/market-demo.parquet` contains 484 anonymous games,
40,342 events and 86,258 historical reference samples in one 992,328-byte file.
The JSON-to-Parquet migration preserved every game payload hash, regulation
terminal state, density score and top-ten selection. It uses typed nested
columns and Zstandard compression, without embedding JSON blobs.

Migration validation: all 70 market tests pass, including importer round trips, schema/hash corruption, immutable publication, selection restrictions and HTTP/session behavior. Ruff, Python compilation and whitespace checks pass. A browser reload confirms the same anonymous top-ten picker and 16-order opening book in Local simulation.
