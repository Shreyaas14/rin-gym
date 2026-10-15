"""Reproduce the pilot report and post-hoc threshold sensitivity tables."""
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent


def main():
    refs=json.loads((ROOT/'reference.json').read_text())
    predictions=json.loads((ROOT/'predictions.json').read_text())
    metrics=json.loads((ROOT/'metrics.json').read_text())
    h=predictions['heuristics']
    responses={m:{json.loads(f.read_text())['pair_id']:json.loads(f.read_text()) for f in (ROOT/'responses'/m).glob('*.json')} for m in ('jev','hybrid')}
    thresholds=[]
    for threshold in (0.5,0.8,0.9,0.95,0.99):
        for method in ('jev','hybrid'):
            tp=fp=reviews=conflicts=0
            for ref in refs:
                pid=ref['id'];hr=h[pid]
                if method=='hybrid' and hr['identity']!='review':decision=hr['identity']
                else:
                    prob=responses[method][pid]['response']['answers']['identity']['probabilities']
                    decision='match' if prob['match']>=threshold else 'nonmatch' if prob['nonmatch']>=threshold else 'review'
                tp+=decision=='match' and ref['identity_gold']=='match'
                fp+=decision=='match' and ref['identity_gold']=='nonmatch'
                reviews+=decision=='review'
                if method=='hybrid' and hr['settlement']=='different':settlement='different'
                else:
                    row=responses[method].get(pid)
                    settlement='different' if row and row['response']['answers']['settlement_compatibility']['probabilities']['different']>=threshold else 'unknown'
                conflicts+=ref['settlement_gold']=='different' and settlement=='different'
            thresholds.append({'threshold':threshold,'method':method,'true_matches':tp,'false_matches':fp,'reviews':reviews,'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/26,'documented_conflicts_detected':conflicts,'scope':'Post-hoc sensitivity analysis on the same pilot pairs, not threshold validation.'})
    (ROOT/'threshold-sensitivity.json').write_text(json.dumps(thresholds,indent=2)+'\n')
    positive_refs=[r for r in refs if r['identity_gold']=='match']
    conflicts=[r for r in positive_refs if r['settlement_gold']=='different']
    identity_argmax_errors=[r for r in refs if responses['jev'][r['id']]['response']['answers']['identity']['choice']!=r['identity_gold']]
    rows=[]
    for ref in refs:
        pid=ref['id'];answer=responses['jev'][pid]['response']['answers']
        row={'pair_id':pid,'family':ref['family'],'kalshi_market':ref['kalshi_id'],'polymarket_market_id':ref['polymarket_id'],'poly_selected_outcome':ref['b']['selected_outcome'],'poly_selected_token_id':ref['b'].get('selected_token_id'),'identity_reference':ref['identity_gold'],'settlement_reference':ref['settlement_gold'],'source_rationale':ref['rationale'],'kalshi_url':ref['a']['url'],'poly_url':ref['b']['url'],'kalshi_terms_url':ref['a']['contract_terms_url'],'raw_jev_identity_choice':answer['identity']['choice'],'raw_jev_p_match':answer['identity']['probabilities']['match'],'raw_jev_settlement_choice':answer['settlement_compatibility']['choice'],'raw_jev_p_compatible':answer['settlement_compatibility']['probabilities']['compatible']}
        for method in predictions:
            row[method+'_identity_decision']=predictions[method][pid]['identity']
            row[method+'_settlement_decision']=predictions[method][pid]['settlement']
        rows.append(row)
    with (ROOT/'pair-review.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    lines=[
        '# Real Kalshi–Polymarket comparison',
        '',
        'October 13, 2026. Model: `jev-1.13.0`. Primary policy and references frozen before inference.',
        '',
        '**Result:** At the predeclared 0.99 model-probability threshold, the hybrid preserves the heuristic baseline’s 19 accepted identity matches, cuts reviews from 21 to 10, and uses 27 rather than 60 inference calls. Raw Jev accepts six identity matches. None of the methods falsely accepts a nonmatching advertised identity at that threshold. This is a pilot about a bounded, deliberately selected set; it does not establish production precision or superiority over a mature matcher.',
        '',
        '## What was compared',
        '',
        'An advertised identity match means the same event, period/instant, selected participant and advertised outcome/threshold bucket. It is a candidate pairing, not a claim of identical payouts. Different feeds, aggregation, rounding or exception payouts can exist inside an advertised identity pair. CPI’s explicit one-decimal precision permits >3.9% to map to >=4.0%. Sports winners do not map to spreads or totals. Polymarket outcome names and token IDs are recorded explicitly, including team-name outcomes rather than YES/NO.',
        '',
        'Strict settlement compatibility is scored separately. A documented counterexample yields `different`; unresolved material terms yield `unknown`. General oracle/venue governance differences alone are not used to reject all pairs.',
        '',
        '## Data and label provenance',
        '',
        '- Official public API catalogs: 1,072 distinct Kalshi markets across 13 selected series and 600 distinct Poly markets from six volume-ranked pages. The extra initial unfiltered catalog was not the evaluation pool.',
        '- Kalshi series source agencies and 13 distinct linked contract PDFs were collected and text-extracted. All 32 targeted catalog/PDF downloads completed and have URL, retrieval-time and SHA-256 entries in `manifest.json`.',
        '- Kalshi KXBTCD, KXFED and KXFEDFUNDSYEAR downloads reached the 200-market per-series cap and returned another cursor; these catalogs are explicitly incomplete. Poly pages were captured from a moving volume-ranked listing, so despite zero duplicate IDs, moving-page omissions cannot be excluded.',
        '- Sixty actual pairs were selected: 26 advertised identity matches and 34 actual near-misses/unrelated controls. No text was rewritten to manufacture negatives. Their source records and PDF terms are frozen in `reference.json`.',
        '- The investigating assistant authored labels from the sources before inference. The labeler and implementation author are the same; there was no independent human adjudication. Generic contract PDFs may change and the fetched versions are not independently certified as the historical rules at original market issuance.',
        '- Most pairs share a small number of rule templates. The 60 cases are correlated and selected for coverage; they are not a random deployment sample or an independent family/time holdout.',
        '',
        '## Methods',
        '',
        'Heuristics use supported-family identity keys, calendar and timezone normalization, exact selected-side mapping, rate-change buckets and CPI one-decimal cutoffs. Missing or unsupported fields cause review. Explicit known source/fallback conflicts are checked deterministically. This is a limited prototype, not an optimized commercial baseline.',
        '',
        'Raw Jev reads frozen rules, full linked Kalshi contract text, source metadata and selected outcomes. It gets one Choice question for nominal identity and one for settlement compatibility. It never receives a reference label, rationale or heuristic decision. Accepted positive and negative model decisions require the relevant option probability to be at least 0.99; the rest go to review/unknown.',
        '',
        'The hybrid auto-decides exact supported identity keys and explicit exclusions, then sends 27 unresolved identity/settlement cases to Jev with normalized source facts alongside raw rules. Deterministic settlement conflicts veto compatibility. For supported exact identity keys it does not require model confirmation. Evaluator/prompt details are in `evaluation-metadata.json` and `evaluate.py`.',
        '',
        '## Primary comparison: 0.99 threshold',
        '',
        '| Method | Accepted true identities / 26 | False identity accepts | Reviews / 60 | Decision coverage | Jev calls |',
        '|---|---:|---:|---:|---:|---:|',
    ]
    for method,label in [('heuristics','Heuristics'),('jev','Raw Jev'),('hybrid','Hybrid')]:
        m=metrics['identity'][method];calls=metrics['model_calls'].get(method,{}).get('successful_calls',0)
        lines.append(f'| {label} | {m["true_matches"]}/26 ({m["recall"]:.1%}) | {m["false_matches"]} | {m["reviews"]}/60 | {m["decision_coverage"]:.1%} | {calls} |')
    lines.extend([
        '',
        'Each method’s accepted identities had zero observed false positives, but the denominators are only 19, 6 and 19. Do not interpret these observations as proven 100% precision. Coverage includes correctly rejected nonmatches as well as accepted matches.',
        '',
        f'Raw Jev’s top-choice identity label, without abstention, agrees with {60-len(identity_argmax_errors)}/60 references (88.3%). At a 0.5 acceptance threshold it accepts 22 true identities and three false identities. The 0.99 policy prevents those false accepts but misses many actual overlaps.',
        '',
        '## Strict settlement findings',
        '',
        'Of the 26 advertised overlaps, 18 have documented payout divergences and eight remain unresolved. There are no independently established fully compatible positive references. Therefore strict-equivalence precision/recall cannot be validated by this set, and no method establishes a safe interchangeable contract pair.',
        '',
        '| Method | Documented conflicts detected among 18 advertised overlaps | Conflicts detected across all 52 different-payout references | Compatible accepts |',
        '|---|---:|---:|---:|',
    ])
    for method,label in [('heuristics','Heuristics'),('jev','Raw Jev'),('hybrid','Hybrid')]:
        detected=sum(predictions[method][p['id']]['settlement']=='different' for p in conflicts)
        sm=metrics['settlement'][method]
        lines.append(f'| {label} | {detected}/18 | {sm["conflicts_detected"]}/52 | {sm["compatible_accepts"]} |')
    lines.extend([
        '',
        'Raw Jev’s top settlement option is `different` on all 60 pairs, including the eight unresolved references, but only three predictions clear 0.99. Its high-threshold conflict-detection count must not be confused with its top-label count. Since there are no confirmed compatible positives, an always-different classifier cannot be meaningfully penalized for rejecting such positives here. Predictions of `different` on unknown references remain unadjudicated.',
        '',
        'Concrete source-backed divergences:',
        '',
        '1. Sports: Kalshi can settle at a fair price if a game does not start within 48 hours; Poly waits for a postponed game to finish and specifies 50-50 for outright cancellation. These nominal game matches are not payout-equivalent under those exceptions.',
        '2. Bitcoin monthly targets: Kalshi uses a cumulative trimmed mean of CF BRTI from issuance, while Poly uses Binance BTC/USDT one-minute high prices over the calendar month. Strict/inclusive strike boundaries also differ.',
        '3. September CPI: Kalshi >3.9% and Poly >=4.0% align at one-decimal publication precision, but Kalshi extends its wait after a shutdown whereas Poly can resolve from a previous month if the relevant release is absent by its fallback deadline.',
        '4. A 25bps Fed action: Poly explicitly rounds a 12.5bps change into the 25bps bracket; Kalshi’s exact 25bps outcome can pay differently.',
        '',
        '## Error inspection and baseline gaps',
        '',
        'Raw Jev mistakes include a hockey rematch on the next day, >4.0% versus >=4.0%, and December 1 versus January 1. It also labels four same-advertised-target Bitcoin pairs as nonmatches, apparently mixing the requested nominal identity task with their genuinely different measurement definitions. That last category depends on the deliberately broad identity definition; it would not be an error in strict payout matching.',
        '',
        'The heuristic baseline misses five Bitcoin overlaps because its full-month parser does not handle Kalshi’s abbreviated `Oct 31, 2026` title format. Poly daily Bitcoin slugs use word dates rather than the parser’s supported ISO form. It also abstains on six moneyline/spread-total negatives because it requires a complete selected-side key before applying all exclusions. These are routine engineering gaps, not evidence that AI is necessary for those cases. The baseline and thresholds were not silently fixed after results became visible. A stronger baseline could reduce the hybrid’s incremental benefit.',
        '',
        '## Post-hoc threshold sensitivity',
        '',
        'The following cutoffs were explored after inspecting results. They are descriptive on the same pilot, not calibrated or held-out operating thresholds. The heuristic decisions stay fixed.',
        '',
        '| Model threshold | Method | True identities accepted / 26 | False accepts | Reviews / 60 |',
        '|---|---|---:|---:|---:|',
    ])
    for row in thresholds:
        lines.append(f'| {row["threshold"]:.2f} | {row["method"]} | {row["true_matches"]}/26 | {row["false_matches"]} | {row["reviews"]}/60 |')
    lines.extend([
        '',
        'At 0.90 the hybrid accepts 20 true identities, zero observed false identities, and leaves seven reviews; raw Jev accepts nine true identities and leaves twenty reviews. At 0.50 the hybrid admits one false identity. Lower thresholds therefore cannot simply be declared safe based on this selected set.',
        '',
        '## Retrieval and runtime',
        '',
        'The shared deterministic candidate ranker places all 26 labeled positive Poly markets in the top five among the frozen 600 Poly markets. This measures retrieval recall only on known positive examples inside these bounded catalogs. It does not establish coverage of all exchange overlaps or precision of the full candidate stream. The reference-pair benchmark also includes deliberate controls outside that top-five stream.',
        '',
        '| Pipeline | Successful inference calls | Reported input tokens | Reported output tokens | Median end-to-end call latency |',
        '|---|---:|---:|---:|---:|',
    ])
    for method,label in [('jev','Raw Jev'),('hybrid','Hybrid')]:
        call=metrics['model_calls'][method]
        lines.append(f'| {label} | {call["successful_calls"]} | {call["input_tokens"]:,} | {call["output_tokens"]:,} | {call["median_end_to_end_seconds"]:.3f}s |')
    lines.extend([
        '',
        'The hybrid uses 55% fewer inference calls and approximately 56% fewer reported input tokens. Both experiments together produced 87 successful inference responses. Calls ran with four concurrent workers. Latency includes client/network/processing time, not model-only latency. Local heuristic work excludes shared collection, PDF extraction and candidate ranking. Token usage is not a verified billing ledger.',
        '',
        '## Recommendation',
        '',
        'For this pilot, use deterministic identity keys and explicit rule-conflict checks first. Jev is useful for resolving unsupported candidates and reducing review work; this experiment does not show a recall gain at the predeclared threshold. Keep identity matches and settlement compatibility separate in the product. Normalize dates and evaluate boundary cases in code, especially strict/inclusive CPI thresholds and game rematches.',
        '',
        'Before deployment claims: repair baseline parsing gaps in a versioned development run, obtain independent reference adjudication, add confirmed compatible positives, and evaluate on new event families and a prospective time holdout with natural candidate negatives. Test probability calibration and repeated/swapped inputs. Do not tune a threshold on this set and present its result as independent validation.',
        '',
        '## Review artifacts',
        '',
        '- `pair-review.csv`: all IDs, selected Poly outcome/token, source URLs, reference rationale, model probabilities and method decisions.',
        '- `reference.json`: complete frozen source records and linked contract text for every pair.',
        '- `manifest.json`, `freeze.json`: source hashes, retrieval timing, reference/implementation hashes.',
        '- `responses/jev/` and `responses/hybrid/`: raw answers, usage, model versions, elapsed times and payload hashes. Gold labels and rationales were excluded from payloads.',
        '- `PROTOCOL.md`: pre-inference scope and decision rules.',
        '- `metrics.json`, `threshold-sensitivity.json`: machine-readable primary and exploratory results.',
        '',
        '## Commands run',
        '',
        '```sh',
        'python3 /Users/alexkan/Desktop/frontiers/outputs/market-entity-resolution-real/collect.py',
        'python3 /Users/alexkan/Desktop/frontiers/outputs/market-entity-resolution-real/prepare.py',
        'python3 /Users/alexkan/Desktop/frontiers/outputs/market-entity-resolution-real/evaluate.py --offline',
        'python3 /Users/alexkan/Desktop/frontiers/outputs/market-entity-resolution-real/evaluate.py --env-file /Users/alexkan/projects/.env',
        'python3 /Users/alexkan/Desktop/frontiers/outputs/market-entity-resolution-real/report.py',
        '```',
        '',
        'No exchange credentials were used and no trades were placed. Only the explicitly approved TypeSafe key was loaded for inference; it was not printed or persisted in artifacts.',
        '',
        '## Pair-by-pair decisions',
        '',
        '| Pair | Family | Identity reference | Heuristics | Raw Jev | Hybrid | Settlement reference |',
        '|---|---|---|---|---|---|---|',
    ])
    for ref in refs:
        pid=ref['id']
        lines.append(f'| {pid} | {ref["family"]} | {ref["identity_gold"]} | {predictions["heuristics"][pid]["identity"]} | {predictions["jev"][pid]["identity"]} | {predictions["hybrid"][pid]["identity"]} | {ref["settlement_gold"]} |')
    (ROOT/'REPORT.md').write_text('\n'.join(lines)+'\n')
    # Verify persisted artifacts and freeze independently of printed model progress.
    freeze=json.loads((ROOT/'freeze.json').read_text())
    for name,digest in freeze['file_sha256'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest
    assert len(refs)==60 and len(responses['jev'])==60 and len(responses['hybrid'])==27
    for ref in refs:
        for method in predictions:assert ref['id'] in predictions[method]
    print(json.dumps({'report':str(ROOT/'REPORT.md'),'review_csv':str(ROOT/'pair-review.csv'),'raw_argmax_correct':60-len(identity_argmax_errors),'verified_frozen_hashes':True,'successful_responses':87,'documented_conflicts_on_identity_matches':len(conflicts)}))


if __name__=='__main__':main()
