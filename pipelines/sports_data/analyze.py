from .collect import load,epoch,dt,norm
from .cache import RAW,ROOT
import json,csv,statistics,datetime

def maybe(n):
 p=RAW/(n+'.json')
 return json.loads(p.read_text()) if p.exists() else {}
def validtime(p):
 try:return epoch(p['wallclock'])
 except (KeyError,ValueError,TypeError):return None
def run():
 ROOT.mkdir(parents=True, exist_ok=True)
 rows=[]
 for m in load('audit_manifest'):
  sport=m['sport'];id=m.get('id','');r={'sport':sport,'game_id':id,'game':m.get('name',m.get('market',{}).get('title','')),'market':m.get('market',{}).get('ticker',m.get('market',{}).get('slug','')),'mapping_error':m.get('match_error','')}
  if 'match_error'in m:rows.append(r);continue
  start=epoch(m['scheduled_start']);r['scheduled_start']=m['scheduled_start']
  r['mapping_note']=m.get('mapping_note','Team names matched within Euro 2024 catalog.')
  if sport=='euro':
   plays=maybe('euro_'+id+'_events');prices=maybe(m['prices_file']).get('history',[])
   g=m['statsbomb_match'];teams=[g['home_team']['home_team_name'],g['away_team']['away_team_name']]
   scores={t:0 for t in teams};regulation={t:0 for t in teams}
   for p in plays:
    if p['period']>4:continue
    goal=p.get('shot',{}).get('outcome',{}).get('name')=='Goal'
    own=p.get('type',{}).get('name')=='Own Goal Against'
    if not (goal or own):continue
    team=p['team']['name'];team=next(t for t in teams if t!=team) if own else team
    scores[team]+=1
    if p['period']<=2:regulation[team]+=1
   outcomes=json.loads(m['market']['outcomes'])
   basis=scores if outcomes[0]!='Yes' else regulation
   selected=outcomes[0] if outcomes[0]!='Yes' else next(t for t in teams if norm(t) in norm(m['market']['question']))
   selected_wins=basis[selected]>basis[next(t for t in teams if t!=selected)]
   r.update(regulation_score=json.dumps(regulation),reconstructed_score_agrees=list(scores.values())==[g['home_score'],g['away_score']],settlement_agrees=selected_wins==(float(json.loads(m['market']['outcomePrices'])[0])==1),contract_basis='Match winner' if outcomes[0]!='Yes' else 'Regulation winner',token_outcome=outcomes[0])
   r.update(events=len(plays),periods=','.join(map(str,sorted(set(p['period'] for p in plays)))),utc_timestamps=0,valid_utc_timestamps=0,market_points=len(prices),market_points_first_120m=sum(start<=p['t']<start+7200 for p in prices),distinct_prices=len(set(p['p'] for p in prices)),first_price_time=prices[0]['t'] if prices else '',last_price_time=prices[-1]['t'] if prices else '',max_price_gap_seconds=max([b['t']-a['t'] for a,b in zip(prices,prices[1:])],default=0),final_score=f"{m['statsbomb_match']['home_score']}-{m['statsbomb_match']['away_score']}",notes='Match-relative timestamps only; 120-minute scheduled window is a coverage screen, not exact alignment.')
  else:
   d=maybe(m['summary_file']);plays=d.get('plays',[]) if sport=='nba' else [c['play'] for c in d.get('commentary',[]) if 'play'in c]
   times=[validtime(p) for p in plays];times=[t for t in times if t is not None];valid=[t for t in times if start-600<=t<=start+21600]
   reversals=[a-b for a,b in zip(times,times[1:]) if b<a]
   r.update(utc_reversals_over_60s=sum(t>60 for t in reversals),max_utc_reversal_seconds=max(reversals,default=0))
   candles=maybe(sport+'_'+id+'_candles').get('candlesticks',[])
   # Only plausibly timed rows are used for the window. Invalid rows remain counted.
   lo=min(valid) if valid else start;hi=max(valid) if valid else start+7200
   in_game=[c for c in candles if lo<=c['end_period_ts']<=hi]
   def nonnull(c,k):return c.get(k,{}).get('close') is not None
   q=[c for c in in_game if nonnull(c,'yes_bid') and nonnull(c,'yes_ask')]
   spreads=[float(c['yes_ask']['close'])-float(c['yes_bid']['close']) for c in q]
   tradebars=[c for c in in_game if float(c.get('volume') or 0)>0]
   comp=d.get('header',{}).get('competitions',[{}])[0]
   scores={c['team']['displayName']:c.get('score') for c in comp.get('competitors',[])}
   winner=[c for c in comp.get('competitors',[]) if c.get('winner')]
   predicted_yes=m['market'].get('yes_sub_title','');matched_winner=any(norm(predicted_yes) in norm(c['team']['displayName']) for c in winner)
   # Draw contract settles Yes exactly when there is no winner and scores match.
   if predicted_yes.lower() in ['tie','draw']:matched_winner=len(scores)==2 and len(set(scores.values()))==1
   r.update(events=len(plays),periods=','.join(map(str,sorted(set(p.get('period',{}).get('number',0) for p in plays)))),utc_timestamps=len(times),valid_utc_timestamps=len(valid),utc_reversals=sum(b<a for a,b in zip(times,times[1:])),first_event_utc=datetime.datetime.fromtimestamp(min(times),datetime.timezone.utc).isoformat() if times else '',last_event_utc=datetime.datetime.fromtimestamp(max(times),datetime.timezone.utc).isoformat() if times else '',market_points=len(in_game),quote_bars=len(q),positive_volume_bars=len(tradebars),expected_minute_bars=max(0,int(hi//60-(lo+59)//60+1)),median_spread=statistics.median(spreads) if spreads else '',noncrossed_quote_bars=sum(s>=0 for s in spreads),distinct_prices=len(set(c['price']['close'] for c in in_game if nonnull(c,'price'))),final_score=json.dumps(scores),settlement_agrees=matched_winner==(m['market']['result']=='yes'),notes='ESPN event wallclock is not first publication/receipt time; candles are interval aggregates.')
  rows.append(r)
 fields=list(dict.fromkeys(k for r in rows for k in r))
 with (ROOT/'audit-results.csv').open('w') as f:
  w=csv.DictWriter(f,fieldnames=fields,lineterminator="\n");w.writeheader();w.writerows(rows)
 (ROOT/'audit-results.json').write_text(json.dumps(rows,indent=2))
 for sport in ['nba','soccer','euro']:
  rs=[r for r in rows if r['sport']==sport];ok=[r for r in rs if not r['mapping_error']]
  s={'sample':len(rs),'matched':len(ok),'events':sum(r.get('events',0) for r in ok),'market_points':sum(r.get('market_points',0) for r in ok),'games_with_prices':sum(r.get('market_points',0)>0 for r in ok),'all_utc_plausible':sum(r.get('events',0)>0 and r['valid_utc_timestamps']==r['events'] for r in ok),'utc_rows':sum(r.get('utc_timestamps',0) for r in ok),'plausible_utc_rows':sum(r.get('valid_utc_timestamps',0) for r in ok),'settlement_agrees':sum(r.get('settlement_agrees',False) for r in ok)}
  if sport!='euro':s.update(quote_bars=sum(r['quote_bars'] for r in ok),expected_bars=sum(r['expected_minute_bars'] for r in ok),positive_volume_bars=sum(r['positive_volume_bars'] for r in ok),utc_reversals=sum(r['utc_reversals'] for r in ok))
  else:s.update(first120m_points=sum(r['market_points_first_120m'] for r in ok),max_gap_seconds=max(r['max_price_gap_seconds'] for r in ok))
  print(sport,json.dumps(s))
 print('problem cases',[(r['sport'],r['game'],r.get('utc_reversals'),r.get('valid_utc_timestamps'),r.get('events'),r.get('settlement_agrees')) for r in rows if not r['mapping_error'] and (r.get('utc_reversals',0)>0 or (r['sport']!='euro' and r['valid_utc_timestamps']!=r['events']) or r.get('settlement_agrees') is False)])
if __name__=='__main__':run()
