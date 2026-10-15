"""Generate an offline review page with frozen rules and no method predictions."""
from pathlib import Path
import json
import random

ROOT=Path(__file__).resolve().parent
pairs=json.loads((ROOT/'pairs.json').read_text())
random.Random(20261014).shuffle(pairs)
records=[]
for p in pairs:
    records.append({'pair_id':p['id'],'family':p['family'],
                    'a':{k:p['a'].get(k) for k in ('id','title','selected_outcome_description','rules','contract_terms_text','contract_terms_url','source_url','url')},
                    'b':{k:p['b'].get(k) for k in ('id','title','selected_outcome','rules','resolution_source','source_url','url')}})
data=json.dumps(records).replace('<','\\u003c').replace('&','\\u0026')
html='''<!doctype html><meta charset="utf-8"><title>Blinded market-pair review</title>
<style>body{max-width:1200px;margin:32px auto;padding:0 24px;font:16px system-ui;background:#f6f5ef;color:#202629}h1{font-size:26px}.cards{display:grid;grid-template-columns:1fr 1fr;gap:20px}.card{background:white;padding:22px;border:1px solid #ccc;border-radius:8px}pre{white-space:pre-wrap;font:14px/1.6 system-ui}textarea{width:100%;min-height:80px;margin:8px 0}button,select,input{font:inherit;padding:8px;margin:5px}label{display:block}#status{color:#426444}details{margin-top:14px}a{color:#075587}@media(max-width:700px){.cards{display:block}}</style>
<h1>Kalshi ↔ Polymarket: independent review</h1>
<p>400 held-out pairs. Predictions and proposed labels are hidden. Your work stays in this browser's local storage until you export it. Use a separate browser profile for a second reviewer.</p>
<p>Identity: <b>match / nonmatch / unknown</b>. Settlement: <b>compatible / different / unknown</b>. Cite frozen clauses; for a difference, state a payout counterexample. Missing terms remain unknown.</p>
<button id="prev">Previous</button><input id="position" type="number" min="1" max="400" value="1"><button id="next">Next</button><button id="export">Export reviewed CSV</button><span id="status"></span>
<p id="heading"></p><div class="cards"><div class="card" id="a"></div><div class="card" id="b"></div></div>
<div class="card" style="margin-top:20px"><label>Reviewer <input id="reviewer"></label><label>Identity <select id="identity_label"><option value="">Unreviewed</option><option>match</option><option>nonmatch</option><option>unknown</option></select></label><textarea id="identity_evidence" placeholder="Identity evidence and source clauses"></textarea><label>Settlement <select id="settlement_label"><option value="">Unreviewed</option><option>compatible</option><option>different</option><option>unknown</option></select></label><textarea id="settlement_counterexample_or_proof" placeholder="Payout counterexample or compatibility proof; cite material rules"></textarea><textarea id="notes" placeholder="Unresolved issues or adjudication notes"></textarea></div>
<script>
const pairs=DATA;const storageKey='kalshi-poly-v2-blind-review';let saved=JSON.parse(localStorage.getItem(storageKey)||'{}');let index=0;
const fields=['reviewer','identity_label','identity_evidence','settlement_label','settlement_counterexample_or_proof','notes'];
function e(id){return document.getElementById(id)}
function node(parent,tag,text){let el=document.createElement(tag);el.textContent=text||'';parent.appendChild(el);return el}
function renderCard(id,m,venue){let c=e(id);c.replaceChildren();node(c,'h2',venue);node(c,'p',m.id);node(c,'h3',m.title);node(c,'p','Compared outcome: '+(m.selected_outcome||m.selected_outcome_description||'Yes'));let a=node(c,'a','Live source (frozen text below)');a.href=m.source_url;a.target='_blank';a.rel='noopener';node(c,'pre',m.rules);if(m.contract_terms_text){let d=node(c,'details','');node(d,'summary','Full linked contract PDF text');node(d,'pre',m.contract_terms_text)}if(m.resolution_source)node(c,'p','Resolution source: '+m.resolution_source)}
function save(){let p=pairs[index],v=saved[p.pair_id]||{};for(let f of fields)v[f]=e(f).value;v.reviewed_at=new Date().toISOString();saved[p.pair_id]=v;localStorage.setItem(storageKey,JSON.stringify(saved));e('status').textContent=Object.values(saved).filter(v=>v.identity_label&&v.settlement_label).length+' / 400 labeled'}
function render(){let p=pairs[index];e('position').value=index+1;e('heading').textContent=p.pair_id+' · '+p.family;renderCard('a',p.a,'Kalshi YES');renderCard('b',p.b,'Selected Polymarket outcome');let v=saved[p.pair_id]||{};for(let f of fields)e(f).value=v[f]||'';e('status').textContent=Object.values(saved).filter(v=>v.identity_label&&v.settlement_label).length+' / 400 labeled'}
for(let f of fields)e(f).addEventListener('input',save);
e('prev').onclick=()=>{index=Math.max(0,index-1);render()};e('next').onclick=()=>{index=Math.min(pairs.length-1,index+1);render()};e('position').onchange=()=>{index=Math.max(0,Math.min(pairs.length-1,Number(e('position').value)-1));render()};
e('export').onclick=()=>{let cols=['pair_id',...fields,'reviewed_at'];let q=x=>'"'+String(x??'').replaceAll('"','""')+'"';let lines=[cols.map(q).join(',')];for(let p of pairs){let v=saved[p.pair_id];if(!v)continue;lines.push(cols.map(k=>q(k==='pair_id'?p.pair_id:v[k])).join(','))}let blob=new Blob([lines.join('\\r\\n')],{type:'text/csv'});let a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='market-pairs-v2-review.csv';a.click();URL.revokeObjectURL(a.href)};
render();
</script>'''.replace('DATA',data)
(ROOT/'review.html').write_text(html)
print(json.dumps({'review_pairs':len(records),'html_bytes':len(html.encode()),'predictions_included':False}))
