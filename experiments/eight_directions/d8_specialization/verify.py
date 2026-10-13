"""Reproduce D8 results, focused tests, and snapshot accounting reconciliation."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from experiments.eight_directions.d8_specialization.analyze import OUT, ROOT, dump, read_lines


def main():
    files = ['results.json', 'analysis_receipts.jsonl', 'selection.json', 'REPORT.md']
    def hashes():
        return {f: hashlib.sha256((OUT / f).read_bytes()).hexdigest() for f in files}
    before = hashes()
    analysis = [sys.executable, '-m', 'experiments.eight_directions.d8_specialization.analyze']
    run = subprocess.run(analysis, cwd=ROOT, text=True, capture_output=True, check=True)
    after = hashes()
    assert before == after
    command = [sys.executable, '-m', 'unittest', 'experiments.eight_directions.d8_specialization.test_analysis', '-v']
    tests = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=True)
    (OUT / 'test-output.txt').write_text(tests.stdout + tests.stderr)
    records = read_lines(ROOT / 'inputs/earlier-run-risk/original-matches.jsonl')
    reference = json.loads((ROOT / 'inputs/earlier-run-risk/matches.json').read_text())['recorded_agent']
    ref = {(r['match_id'], r['repeat']): r for r in reference}
    for match in records:
        assert abs(match['pnl'] - sum(s['pnl'] for s in match['steps'])) < 1e-12
        assert abs(match['pnl'] - ref[(match['match_id'], match['repeat'])]['pnl']) < 1e-12
        assert abs(match['cost'] - sum(s['cost'] for s in match['steps'])) < 1e-12
        assert match['trades'] == sum(s['trades'] for s in match['steps'])
    protocol = json.loads((OUT / 'protocol.json').read_text())
    fit, later = set(protocol['fit_match_ids']), set(protocol['assessment_match_ids'])
    assert len(fit) == 39 and len(later) == 33 and not fit & later
    results = json.loads((OUT / 'results.json').read_text())
    assert results['classification'] == 'negative' and results['selected_candidate'] is None
    assert results['observations'] == 1077
    assert results['red_prefix_parse_supported_observations'] == 1077
    assert results['guard_before'] == results['guard_after']
    verification = {'analysis_command': analysis, 'analysis_exit_code': run.returncode,
                    'analysis_stdout': run.stdout.strip(), 'tests_command': command,
                    'tests_exit_code': tests.returncode, 'test_count': 5,
                    'deterministic_rerun': before == after, 'deterministic_output_sha256': after,
                    'recorded_pnl_cost_and_turnover_reconciled_match_replays': len(records),
                    'recorded_agent_reference_reconciled_match_replays': len(reference),
                    'partition_whole_match_disjoint': True, 'fit_matches': len(fit),
                    'assessment_matches': len(later), 'guard_unchanged': True,
                    'owned_source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in Path(__file__).parent.glob('*.py')}, 'paid_requests': 0}
    dump(OUT / 'verification.json', verification)
    print(json.dumps({'tests': 5, 'reconciled_replays': len(records),
                      'deterministic_rerun': True, 'seal_unchanged': True}))


if __name__ == '__main__':
    main()
