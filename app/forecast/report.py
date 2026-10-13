"""A standalone, offline dashboard and a concise Markdown results report."""
import json
from .data import ARTIFACTS, OUTPUT, write

LABELS = {'climatology': 'Training climatology', 'pregame_market': 'Pregame market, frozen', 'poisson_training': 'Poisson · training rates', 'poisson_pregame': 'Poisson · pregame initialized', 'poisson_calibrated': 'Poisson · validation calibrated', 'polymarket_live': 'Live Polymarket'}


def create_report(manifest, cases, predictions, metrics, selection, coverage, reliability, breakdowns):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    headline = metrics['test']['0']
    primary = headline['poisson_calibrated']
    delta = primary['brier_delta_vs_live']
    rows = []
    for name, label in LABELS.items():
        m = headline[name]
        rows.append(f"| {label} | {m['brier']:.4f} | {m['log_loss']:.4f} | [{m['brier_ci95'][0]:.4f}, {m['brier_ci95'][1]:.4f}] |")
    counts = {s: len([m for m in manifest if m['split'] == s and m['status'] == 'ok']) for s in ['train', 'validation', 'test']}
    exclusions = sum(r.get('stale_or_missing', 0) for r in coverage)
    markdown = f"""# World Cup replay forecast benchmark

Built from cached FIFA event timelines and public Polymarket history. Scope: the 72 group-stage games, regulation time plus stoppage time; home/draw/away outcomes.

## Held-out results

{primary['games']} test games, {primary['snapshots']:,} snapshots. Each match receives equal weight. Lower Brier and log loss are better. Brier is the **sum across three classes**, range 0–2; log loss uses natural logs and a 1e-6 floor on the realized-outcome probability.

| Forecaster | Brier | Log loss | 95% match-bootstrap Brier interval |
|---|---:|---:|---|
{chr(10).join(rows)}

Calibrated simulator minus live-market Brier: **{delta['mean']:+.4f}**, paired 95% match-bootstrap interval **[{delta['ci95'][0]:+.4f}, {delta['ci95'][1]:+.4f}]**. Positive means the simulator is worse. This interval measures variation across the sampled games, not uncertainty about source timestamps or generalization to another tournament.

## Split and selection

- Training: June 11–21 UTC, {counts['train']} games. Fit goal rates, period-duration distributions and climatology here only.
- Validation: June 22–24 UTC, {counts['validation']} games. Select one temperature from the fixed grid by equal-match-weighted Brier, using zero-delay observations.
- Test: June 25–28 UTC, {counts['test']} games. No parameter selection on this set.
- Selected temperature: **{selection['selected_temperature']}**. Validation results for this selected variant are selection-biased; the held-out test remains the comparison.
- Membership was fixed by calendar date before forecast scoring; concurrent matches are kept in the same split. This is a retrospective chronological holdout, not a prospective or blinded benchmark. Some source matches were inspected in the earlier data audit.

## Models

The simple simulator models future home and away goals as independent Poisson counts. The training-only variant estimates two constant per-minute goal rates from training games. The pregame variant finds a pair of full-match goal intensities that approximates the normalized pregame market distribution, then scales them by estimated remaining time. The calibrated variant applies the temperature selected on validation probabilities.

Remaining time uses training-only empirical half durations conditional on a half still being in progress. It includes stoppage time within each half but excludes halftime. When elapsed time exceeds all training durations, it retains one minute of uncertainty rather than declaring a deterministic result before the whistle. It does not read a test game's future whistle time. It ignores tactics, player strength beyond the pregame prior, red cards, shots and spatial coordinates.

Forecast scores use exact Poisson outcome probabilities, avoiding Monte Carlo noise. The simulator CLI independently samples goal-count continuations and example goal timelines under the same model. Those synthetic outcomes are not used as evaluation labels.

## Replay and timestamp rules

- One snapshot every 60 seconds, starting two minutes after actual kickoff; halftime and completed games are excluded. The two-minute burn-in supports the same snapshots at all tested observation delays.
- Features come from the event prefix at decision time minus 0, 30, 60 or 120 seconds. All delays use the same retained snapshots. Live market prices stay contemporaneous in delay stress tests.
- Pregame probabilities are frozen using samples at or before one minute before actual kickoff. Each must be no older than 180 seconds at that cutoff.
- Live comparison uses only samples at or before decision time, no older than 90 seconds. No future nearest-neighbor matching or interpolation. Missing market snapshots are excluded from **all** compared models: {exclusions} excluded here.
- Independent home/draw/away Yes prices are divided by their sum to form a coherent three-class distribution. Original values, sample timestamps, ages and sums are retained in the CSV. Market normalization can change individual prices.
- Several same-timestamp shot/save/goal events carry conflicting score fields. The harness takes the maximum score within that already-observable batch; it rejects score decreases between different timestamp batches. The mapping manifest records these conflicts.
- Targets come from the FIFA regulation-end score and are cross-checked with the scoreboard and all three contract settlements. Targets are kept outside the forecaster's observation object.

**Timing limitation:** archived event timestamps do not establish first publication or receipt time. Delayed-observation scenarios are stress tests, not a correction that proves live tradability or information availability. Scorekeeping errors, late edits or prices reacting before archived events can affect results.

## Reading the evidence

The main table averages each game's snapshot losses first, then averages the games. Confidence intervals use 4,000 whole-match bootstrap draws. Paired comparisons resample model-minus-market differences on the same matches. Calibration curves are descriptive and also use equal game weights; do not treat thousands of snapshots as thousands of independent outcomes. Shared teams and tournament context can create dependence even between games, so the bootstrap is only an approximate within-tournament uncertainty estimate.

`report.html` lets you inspect delay sensitivity, game-phase/score-margin results, classwise calibration, and individual held-out games. The individual-game chart reveals the full historical forecast path for analysis; it is not an input to any prediction.

## Artifacts

Generated tables and metrics are in `data/processed/forecast/`; the HTML report and screenshots are in `artifacts/forecast-eval/`. The large prediction CSV is regenerated locally and excluded from Git.

- `predictions.csv`: every forecast, target and timestamp audit field; one row per match/snapshot/delay.
- `metrics.json`: aggregate and per-game scores, intervals, paired differences.
- `calibration.json`, `breakdowns.json`, `coverage.json`: diagnostics and exclusions.
- `model.json`, `selection.json`, `pregame_fits.json`: fitted parameters and validation selection.
- `run_manifest.json`: configuration, source-manifest hash, code hashes and assumptions.
- `../../data/manifests/forecast/manifest.json`, `../../data/manifests/forecast/splits.json`: cross-source joins, eligibility and frozen membership.

This is a working benchmark and a simple forecasting baseline. Twenty test games do not establish broad reliability, and reusing this test set for repeated model development would turn it into a validation set.
"""
    (ARTIFACTS / 'REPORT.md').write_text(markdown)
    games = []
    for m in manifest:
        if m['split'] != 'test' or m['status'] != 'ok':
            continue
        indices = [i for i, c in enumerate(cases) if c['match_id'] == m['match_id']]
        games.append({'id': m['match_id'], 'name': m['home'] + ' vs ' + m['away'], 'score': m['final_score'], 'target': m['target'], 'points': [
            {'utc': cases[i]['decision_time'], 'phase': cases[i]['phase'], 'minute': round(cases[i]['phase_minute'], 2), 'score': [cases[i]['observations'][0].home_goals, cases[i]['observations'][0].away_goals], 'sim': predictions[0]['poisson_calibrated'][i].tolist(), 'market': predictions[0]['polymarket_live'][i].tolist(), 'pregame': predictions[0]['pregame_market'][i].tolist(), 'age': cases[i]['market']['max_age']} for i in indices]})
    payload = {'labels': LABELS, 'metrics': metrics, 'selection': selection, 'breakdowns': breakdowns, 'calibration': reliability, 'games': games}
    html = TEMPLATE.replace('__PAYLOAD__', json.dumps(payload).replace('</', '<\\/'))
    (ARTIFACTS / 'report.html').write_text(html)


TEMPLATE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>World Cup · Forecast Lab</title><link rel="icon" href="data:,">
<style>
:root{--ink:#163536;--muted:#627978;--paper:#f4f1e8;--line:#d7ddd3;--blue:#2765bb;--orange:#bd6037}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:system-ui,-apple-system,sans-serif}main{max-width:1220px;margin:auto;padding:42px 30px 70px}header{border-bottom:1px solid var(--line);padding-bottom:28px}.eyebrow{font-size:12px;letter-spacing:2px;font-weight:700;color:var(--muted)}h1{font-size:46px;letter-spacing:-2px;margin:12px 0}h2{font-size:23px;letter-spacing:-.6px;margin:0 0 14px}p{line-height:1.65;color:var(--muted)}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:24px 0}.card,section{background:#fffdf7;border:1px solid var(--line);border-radius:14px;padding:24px}.card strong{display:block;font-size:34px;margin:8px 0}.card span{font-size:13px;color:var(--muted)}section{margin:20px 0}.row{display:flex;gap:18px;align-items:center;justify-content:space-between;flex-wrap:wrap}select{padding:10px 14px;border:1px solid var(--line);background:white;border-radius:7px;color:var(--ink);font:inherit}.small{font-size:13px;line-height:1.6;color:var(--muted)}table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:13px 10px;border-bottom:1px solid var(--line);text-align:right;font-variant-numeric:tabular-nums}th:first-child,td:first-child{text-align:left}th{color:var(--muted);font-size:12px}.primary{background:#eef3e9}.chart{width:100%;height:auto;display:block}.legend{display:flex;gap:24px;font-size:13px;color:var(--muted);margin:8px 0}.legend i{display:inline-block;width:24px;height:3px;margin:0 7px 3px 0}.notes{padding:18px 20px;background:#e9eee2;border-left:4px solid #738d71;font-size:14px;line-height:1.65}.downloads a{margin-right:20px;color:var(--blue)}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.overflow{overflow:auto}@media(max-width:760px){h1{font-size:34px}.cards,.grid{grid-template-columns:1fr}main{padding:24px 16px}.card strong{font-size:28px}section{padding:18px}}
</style></head><body><main>
<header><div class="eyebrow">FIELD / FORECAST · RESEARCH BENCHMARK</div><h1>Can a simple simulator beat the market?</h1><p>Chronological replay of World Cup 2026 group-stage matches. Train on 39 games, calibrate on 13, evaluate on 20 held-out games.</p><div class="small">Regulation + stoppage time · home / draw / away · no betting or execution assumptions</div></header>
<div class="cards"><div class="card"><div class="eyebrow">HELD-OUT EVIDENCE</div><strong id="count"></strong><span id="snapshotCount"></span></div><div class="card"><div class="eyebrow">CALIBRATED SIMULATOR BRIER</div><strong id="simScore"></strong><span>Lower is better · 0–2 multiclass scale</span></div><div class="card"><div class="eyebrow">LIVE MARKET BRIER</div><strong id="marketScore"></strong><span>Normalized home/draw/away Yes prices</span></div></div>
<div class="notes" id="verdict"></div>
<section><div class="row"><h2>Held-out leaderboard</h2><label>Event observation delay <select id="delay"><option value="0">0 seconds</option><option value="30">30 seconds</option><option value="60">60 seconds</option><option value="120">120 seconds</option></select></label></div><p class="small">All methods share the same snapshots. Delays affect simulator observations; live market prices remain contemporaneous. Each game has equal weight.</p><div class="overflow"><table><thead><tr><th>Forecaster</th><th>Brier</th><th>Log loss</th><th>95% Brier interval</th><th>Δ vs live market</th></tr></thead><tbody id="leaderboard"></tbody></table></div></section>
<section><div class="row"><h2>Where does it work?</h2><select id="slice"></select></div><div class="overflow"><table><thead><tr><th>Forecaster</th><th>Games</th><th>Brier</th><th>Log loss</th></tr></thead><tbody id="slices"></tbody></table></div></section>
<section><div class="row"><h2>Inspect a held-out game</h2><select id="game"></select><select id="outcome"><option value="0">Home win</option><option value="1">Draw</option><option value="2">Away win</option></select></div><p class="small" id="gameInfo"></p><div class="legend"><span><i style="background:#2765bb"></i>Calibrated simulator</span><span><i style="background:#bd6037"></i>Live market</span><span><i style="background:#86918e"></i>Pregame, frozen</span></div><svg id="gameChart" class="chart" viewBox="0 0 1100 310" role="img" aria-label="Forecast probabilities over match time"></svg><p class="small">UTC horizontal axis; halftime creates a gap. Full trajectories are shown only for post-hoc inspection. Forecasts were generated from information prefixes.</p></section>
<section><div class="row"><h2>Calibration on held-out games</h2><select id="calOutcome"><option value="home">Home win</option><option value="draw">Draw</option><option value="away">Away win</option></select></div><div class="legend"><span><i style="background:#2765bb"></i>Calibrated simulator</span><span><i style="background:#bd6037"></i>Live market</span></div><svg id="calChart" class="chart" viewBox="0 0 1100 360" role="img" aria-label="Calibration plot: predicted probability against observed frequency"></svg><p class="small">Diagonal = calibrated. Ten probability bins with equal-match-weighted contributions. Curves are descriptive; repeated snapshots are not independent outcomes. Hover points for bin support.</p></section>
<section><h2>How to interpret this run</h2><div class="grid"><div><p><b>Simulator.</b> Independent future home/away goal counts. Training games supply scoring rates and half-duration distributions. The stronger variant initializes rates from the frozen pregame market. Exact Poisson probabilities remove simulation noise.</p><p id="temperature" class="small"></p></div><div><p><b>Limits.</b> Archived FIFA timestamps are not verified receipt times. The model ignores player movements, cards, tactical changes and shot quality. Market probabilities are sampled and normalized. This is one tournament with only 20 test games.</p><p class="small">Confidence intervals resample whole matches, not snapshots. Shared teams may still induce dependence. The test set should not become a tuning target.</p></div></div><div class="downloads"><a href="REPORT.md">Full methodology</a><a href="../../data/processed/forecast/predictions.csv">Forecast CSV</a><a href="../../data/processed/forecast/metrics.json">Metrics JSON</a><a href="../../data/processed/forecast/run_manifest.json">Run provenance</a></div></section>
</main><script>
const D=__PAYLOAD__;
const $=id=>document.getElementById(id), fmt=v=>Number(v).toFixed(4), ci=a=>`[${fmt(a[0])}, ${fmt(a[1])}]`;
const base=D.metrics.test['0'], primary=base.poisson_calibrated, delta=primary.brier_delta_vs_live;
$('count').textContent=primary.games+' games';$('snapshotCount').textContent=primary.snapshots.toLocaleString()+' snapshots · whole-game holdout';$('simScore').textContent=fmt(primary.brier);$('marketScore').textContent=fmt(base.polymarket_live.brier);
$('verdict').textContent=`Simulator minus market Brier: ${delta.mean>=0?'+':''}${fmt(delta.mean)}; paired 95% interval ${ci(delta.ci95)}. ${delta.mean>0?'The simulator scores worse in this test sample.':'The simulator scores better in this test sample.'} ${delta.ci95[0]<=0&&delta.ci95[1]>=0?'The interval includes zero; this run does not resolve the difference.':'This is a within-tournament comparison, not a claim of general superiority.'}`;
$('temperature').textContent=`Temperature ${D.selection.selected_temperature} selected using validation Brier only. It stays fixed across test games and delay scenarios.`;
function table(){const ms=D.metrics.test[$('delay').value];$('leaderboard').innerHTML='';Object.keys(D.labels).forEach(n=>{const m=ms[n],tr=document.createElement('tr');if(n==='poisson_calibrated'||n==='polymarket_live')tr.className='primary';[D.labels[n],fmt(m.brier),fmt(m.log_loss),ci(m.brier_ci95),(m.brier_delta_vs_live.mean>=0?'+':'')+fmt(m.brier_delta_vs_live.mean)].forEach(v=>{const td=document.createElement('td');td.textContent=v;tr.appendChild(td)});$('leaderboard').appendChild(tr)})}
Object.keys(D.breakdowns).forEach(k=>$('slice').add(new Option(k.replaceAll('_',' '),k)));
function slices(){const ms=D.breakdowns[$('slice').value];$('slices').innerHTML='';Object.keys(D.labels).forEach(n=>{const m=ms[n],tr=document.createElement('tr');[D.labels[n],m.games,fmt(m.brier),fmt(m.log_loss)].forEach(v=>{const td=document.createElement('td');td.textContent=v;tr.appendChild(td)});$('slices').appendChild(tr)})}
const NS='http://www.w3.org/2000/svg';function el(svg,name,attrs,text){const e=document.createElementNS(NS,name);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));if(text!==undefined)e.textContent=text;svg.appendChild(e);return e}
function axes(svg,x,y,w,h){svg.innerHTML='';[0,.25,.5,.75,1].forEach(v=>{el(svg,'line',{x1:x,y1:y+h*(1-v),x2:x+w,y2:y+h*(1-v),stroke:'#d7ddd3'});el(svg,'text',{x:x-10,y:y+h*(1-v)+4,'text-anchor':'end',fill:'#627978','font-size':12},Math.round(v*100)+'%')})}
D.games.forEach(g=>$('game').add(new Option(g.name,g.id)));
function game(){const g=D.games.find(x=>x.id===$('game').value), k=Number($('outcome').value), svg=$('gameChart');$('gameInfo').textContent=`Final score ${g.score.join('–')} · ${g.points.length} shared snapshots · maximum sampled-price age ${Math.max(...g.points.map(p=>p.age)).toFixed(1)}s`;const x=65,y=20,w=1000,h=240;axes(svg,x,y,w,h);const lo=g.points[0].utc,hi=g.points.at(-1).utc,xx=t=>x+(t-lo)/(hi-lo)*w;[['pregame','#86918e'],['market','#bd6037'],['sim','#2765bb']].forEach(([name,color])=>{let path='',prev=null;g.points.forEach(p=>{const a=xx(p.utc),b=y+(1-p[name][k])*h;if(!prev||p.utc-prev.utc>120)path+=`M${a},${b}`;else path+=`H${a}V${b}`;prev=p});el(svg,'path',{d:path,fill:'none',stroke:color,'stroke-width':name==='pregame'?1.5:2.4,'stroke-dasharray':name==='pregame'?'5 4':'none'})});for(let i=0;i<=5;i++){const t=lo+(hi-lo)*i/5;el(svg,'text',{x:xx(t),y:285,'text-anchor':'middle',fill:'#627978','font-size':12},new Date(t*1000).toISOString().slice(11,16)+' UTC')}}
function cal(){const svg=$('calChart'),x=65,y=20,w=1000,h=285;axes(svg,x,y,w,h);el(svg,'line',{x1:x,y1:y+h,x2:x+w,y2:y,stroke:'#a5b2a8','stroke-dasharray':'6 5'});[['poisson_calibrated','#2765bb'],['polymarket_live','#bd6037']].forEach(([name,color])=>{const bins=D.calibration[name][$('calOutcome').value];el(svg,'polyline',{points:bins.map(b=>`${x+b.predicted*w},${y+(1-b.observed)*h}`).join(' '),fill:'none',stroke:color,'stroke-width':2});bins.forEach(b=>{const c=el(svg,'circle',{cx:x+b.predicted*w,cy:y+(1-b.observed)*h,r:5,fill:color});const title=document.createElementNS(NS,'title');title.textContent=`${D.labels[name]} · ${b.games} games · ${b.snapshots} snapshots · predicted ${(100*b.predicted).toFixed(1)}% / observed ${(100*b.observed).toFixed(1)}%`;c.appendChild(title)})});[0,.25,.5,.75,1].forEach(v=>el(svg,'text',{x:x+v*w,y:330,'text-anchor':'middle',fill:'#627978','font-size':12},Math.round(v*100)+'% predicted'))}
$('delay').onchange=table;$('slice').onchange=slices;$('game').onchange=game;$('outcome').onchange=game;$('calOutcome').onchange=cal;table();slices();game();cal();
</script></body></html>'''
