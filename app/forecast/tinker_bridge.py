"""Optional Tinker integration; core data validation needs neither Tinker nor MLflow."""
from copy import deepcopy
import json
from pathlib import Path

from . import governance as gov
from .metrics import scores, summarize

PROMPT = ('Forecast the regulation result from this observation. Return only a JSON array '
          'of three probabilities in home, draw, away order, summing to one.')


def messages(observation):
    # Labels, teacher answers, match identity and arbitrary text never enter the prompt.
    return [{'role': 'system', 'content': PROMPT},
            {'role': 'user', 'content': gov.canonical(observation['observation'])}]


def validate_probabilities(value):
    if not isinstance(value, list) or len(value) != 3 or any(type(p) not in [int, float] for p in value):
        raise ValueError('Expected three numeric probabilities, not strings or booleans')
    scores([value], [0])
    return value


def validate_rows(rows, training=False):
    if not isinstance(rows, list) or not rows:
        raise ValueError('Dataset must contain records')
    seen, matches = set(), {}
    for row in rows:
        gov.exact_keys(row, ['match_id', 'case_id', 'kickoff_utc', 'label_available_utc',
                             'observation', 'target'] + (['teacher_probabilities'] if training else []))
        mid, cid = gov.identifier(row['match_id']), gov.identifier(row['case_id'])
        if cid in seen:
            raise ValueError('Duplicate dataset case ID')
        seen.add(cid)
        envelope = row['observation']
        if not isinstance(envelope, dict) or not isinstance(envelope.get('observation'), dict) or 'cutoff' not in envelope['observation']:
            raise ValueError('Missing observation envelope or cutoff')
        cutoff = envelope['observation']['cutoff']
        if type(cutoff) not in [int, float]:
            raise ValueError('Observation cutoff must be numeric')
        kickoff, available = gov.timestamp(row['kickoff_utc']), gov.timestamp(row['label_available_utc'])
        if not kickoff <= cutoff < available <= gov.timestamp(gov.utc_now()):
            raise ValueError('Require kickoff <= observation cutoff < available label <= now')
        gov.safe_observation(row['observation'], cutoff, gov.timestamp(gov.utc_now()))
        if type(row['target']) is not int or row['target'] not in [0, 1, 2]:
            raise ValueError('Target must be 0, 1 or 2')
        identity = (row['kickoff_utc'], row['label_available_utc'], row['target'])
        if mid in matches and matches[mid] != identity:
            raise ValueError('Conflicting match metadata or labels')
        matches[mid] = identity
        if training:
            validate_probabilities(row['teacher_probabilities'])
    return set(matches)


def load_datasets(train_path, validation_path, workspace=None):
    train, validation = [json.loads(Path(p).read_text()) for p in [train_path, validation_path]]
    train_ids, val_ids = validate_rows(train, True), validate_rows(validation)
    if train_ids & val_ids:
        raise ValueError('Training and validation must be disjoint by match')
    if {r['case_id'] for r in train} & {r['case_id'] for r in validation}:
        raise ValueError('Training and validation case IDs overlap')
    if max(gov.timestamp(r['label_available_utc']) for r in train) >= min(gov.timestamp(r['kickoff_utc']) for r in validation):
        raise ValueError('All training labels must precede the first validation kickoff')
    ledger = gov.Ledger(workspace)
    # Require explicit init, not a silent fallback to an unprotected workspace.
    ledger.audit_dataset(train_ids | val_ids, gov.digest({'train': train, 'validation': validation}))
    return deepcopy(train), deepcopy(validation)


def read_manifest(path):
    manifest = json.loads(Path(path).read_text())
    gov.exact_keys(manifest, ['checkpoint', 'model_name', 'renderer', 'max_tokens', 'temperature'])
    if not isinstance(manifest['checkpoint'], str) or not manifest['checkpoint'].startswith('tinker://') or '/sampler_weights/' not in manifest['checkpoint']:
        raise ValueError('Use a saved Tinker sampler checkpoint, not mutable training weights')
    for key in ['model_name', 'renderer']:
        if not isinstance(manifest[key], str) or not manifest[key].strip():
            raise ValueError('Specify model_name and renderer')
    if type(manifest['max_tokens']) is not int or not 1 <= manifest['max_tokens'] <= 4096:
        raise ValueError('max_tokens must be in [1, 4096]')
    if type(manifest['temperature']) not in [float, int] or manifest['temperature'] != 0:
        raise ValueError('Use temperature=0 for this frozen forecasting protocol')
    return manifest


def make_renderer(model_name, renderer_name):
    from tinker_cookbook.renderers import get_renderer
    from tinker_cookbook.tokenizer_utils import get_tokenizer
    return get_renderer(renderer_name, get_tokenizer(model_name))


async def sample_probabilities(client, renderer, observation, max_tokens):
    from tinker import SamplingParams
    from tinker_cookbook.renderers import get_text_content
    result = await client.sample_async(
        prompt=renderer.build_generation_prompt(messages(observation)), num_samples=1,
        sampling_params=SamplingParams(max_tokens=max_tokens, temperature=0,
                                       stop=renderer.get_stop_sequences()))
    if len(result.sequences) != 1:
        raise ValueError('Expected one sample')
    response, complete = renderer.parse_response(result.sequences[0].tokens)
    if not complete.is_clean:
        raise ValueError('Incomplete model response')
    probabilities = json.loads(get_text_content(response))
    return validate_probabilities(probabilities)


async def predict(workspace, candidate, case_id, observation, manifest_path):
    manifest = read_manifest(manifest_path)
    ledger = gov.Ledger(workspace)
    # This durable claim is made before client construction, tokenizer download or sampling.
    started = ledger.begin_tinker_prediction(candidate, case_id, observation, manifest_path, manifest)
    probabilities, error = None, None
    try:
        import tinker
        renderer = make_renderer(manifest['model_name'], manifest['renderer'])
        client = tinker.ServiceClient().create_sampling_client(model_path=manifest['checkpoint'])
        probabilities = await sample_probabilities(client, renderer, observation, manifest['max_tokens'])
    except Exception as exc:
        # API exception text may contain request headers; only record its type.
        error = 'Tinker prediction failed: ' + type(exc).__name__
    return ledger.record(candidate, case_id, observation, probabilities, error,
                         attempt_sha256=started['sha256'])


def make_dataset_builder(train_path, validation_path, model_name, renderer_name, workspace=None, batch_size=8):
    """Cookbook builder; validate before tokenizer loading, then guard every batch."""
    workspace = gov.Ledger(workspace).workspace.resolve()
    train_path, validation_path = Path(train_path).resolve(), Path(validation_path).resolve()
    train, validation = load_datasets(train_path, validation_path, workspace)
    expected = gov.digest({'train': train, 'validation': validation})
    import chz
    from tinker_cookbook.supervised.types import SupervisedDataset, SupervisedDatasetBuilder
    from tinker_cookbook.supervised.data import conversation_to_datum
    from tinker_cookbook.renderers import TrainOnWhat
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError('batch_size must be positive')

    class Dataset(SupervisedDataset):
        def __init__(self, rows, renderer, batch_size, workspace):
            self._rows_json, self.renderer = gov.canonical(rows), renderer
            self.batch_size, self.workspace = batch_size, workspace

        def __len__(self):
            return (len(json.loads(self._rows_json)) + self.batch_size - 1) // self.batch_size

        def get_batch(self, index):
            if not 0 <= index < len(self):
                raise IndexError(index)
            rows = json.loads(self._rows_json)[index * self.batch_size:(index + 1) * self.batch_size]
            gov.Ledger(self.workspace).audit_dataset([r['match_id'] for r in rows], gov.digest(rows))
            return [conversation_to_datum(
                messages(r['observation']) + [{'role': 'assistant', 'content': gov.canonical(r['teacher_probabilities'])}],
                self.renderer, max_length=None, train_on_what=TrainOnWhat.ALL_ASSISTANT_MESSAGES) for r in rows]

        def set_epoch(self, seed=0):
            # Preserve chronology and deterministic batches across resumes.
            pass

    @chz.chz
    class Builder(SupervisedDatasetBuilder):
        train_file: str = str(train_path)
        validation_file: str = str(validation_path)
        dataset_sha256: str = expected
        model: str = model_name
        renderer: str = renderer_name
        workspace_path: str = str(gov.Ledger(workspace).workspace.resolve())
        batch_size_value: int = batch_size

        def __call__(self):
            train, validation = load_datasets(self.train_file, self.validation_file, self.workspace_path)
            if gov.digest({'train': train, 'validation': validation}) != self.dataset_sha256:
                raise ValueError('Dataset changed after builder configuration')
            return Dataset(train, make_renderer(self.model, self.renderer), self.batch_size_value, self.workspace_path), None
    return Builder()


def make_evaluator(train_path, validation_path, model_name, renderer_name, workspace=None, max_tokens=128):
    """Factory for Config.evaluator_builders; never use this for the protected cohort."""
    workspace = gov.Ledger(workspace).workspace.resolve()
    train_path, validation_path = Path(train_path).resolve(), Path(validation_path).resolve()
    train, validation = load_datasets(train_path, validation_path, workspace)
    expected = gov.digest({'train': train, 'validation': validation})
    from tinker_cookbook.eval.evaluators import SamplingClientEvaluator

    class Evaluator(SamplingClientEvaluator):
        async def __call__(self, sampling_client):
            train, rows = load_datasets(train_path, validation_path, workspace)
            if gov.digest({'train': train, 'validation': rows}) != expected:
                raise ValueError('Dataset changed after evaluator configuration')
            renderer = make_renderer(model_name, renderer_name)
            probabilities = []
            for row in rows:
                probabilities.append(await sample_probabilities(sampling_client, renderer, row['observation'], max_tokens))
            report = summarize(probabilities, [r['target'] for r in rows], [r['match_id'] for r in rows], gov.SCORING)
            return {'forecast/' + k: float(report[k]) for k in ['brier', 'log_loss', 'accuracy', 'games', 'snapshots']}
    return Evaluator
