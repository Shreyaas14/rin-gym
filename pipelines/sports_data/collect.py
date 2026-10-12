from .cache import fetch, RAW
import json, concurrent.futures, datetime, re
from zoneinfo import ZoneInfo

def load(n):return json.loads((RAW/(n+'.json')).read_text())
def dt(s):return datetime.datetime.fromisoformat(s.replace('Z','+00:00'))
def epoch(s):return int(dt(s).timestamp())
def norm(s):return re.sub(r'[^a-z0-9]','',s.lower().replace('czech republic','czechia').replace('türkiye','turkey'))

def collect():
 manifests=[];urls={}
 for sport,league in [('nba','basketball/nba'),('soccer','soccer/eng.1')]:
  schedules={}
  for p in RAW.glob(sport+'_schedule_*.json'):
   for e in json.loads(p.read_text()).get('events',[]):schedules[e['id']]=e
  for m in load(sport+'_sample_markets'):
   mapping_note='Teams and original ticker date agree with ESPN schedule.'
   title=re.sub(r'^Game \d+: ','',m['title']).replace(' Winner?','')
   teams=title.split(' at ' if sport=='nba' else ' vs ')
   candidates=[e for e in schedules.values() if all(any(norm(t) in norm(c['team']['displayName']) for c in e['competitions'][0]['competitors']) for t in teams)]
   day=datetime.datetime.strptime(m['event_ticker'].split('-')[1][:7],'%y%b%d').date()
   candidates=[e for e in candidates if dt(e['date']).astimezone(ZoneInfo('America/New_York')).date()==day]
   # Manually verified reschedule: rules retain May 17, while the market closed
   # May 15 at 21:00 UTC and ESPN records the same teams May 15 at 19:00 UTC.
   if sport=='soccer' and m['event_ticker']=='KXEPLGAME-26MAY17AVLLFC':
    candidates=[schedules['740957']]
    mapping_note='Explicit reschedule override: original May 17 ticker, actual May 15 game; verified teams, market rules, close time and ESPN schedule.'
   if len(candidates)!=1:
    manifests.append({'sport':sport,'market':m,'match_error':f'{len(candidates)} schedule matches'});continue
   e=candidates[0];id=e['id'];name=sport+'_'+id
   manifests.append({'sport':sport,'id':id,'name':e['name'],'scheduled_start':e['date'],'market':m,'summary_file':name+'_summary','mapping_note':mapping_note})
   urls[name+'_summary']=f'https://site.api.espn.com/apis/site/v2/sports/{league}/summary?event={id}'
   start=epoch(e['date']);end=epoch(m['close_time'])
   # Include the scheduled match and its close, capped to six hours to flag bad alignments.
   urls[name+'_candles']=f"https://api.elections.kalshi.com/trade-api/v2/historical/markets/{m['ticker']}/candlesticks?start_ts={start-600}&end_ts={min(end,start+21600)}&period_interval=1"
 # 20 most recent Euro 2024 matches, match on title rather than slug (one slug has the wrong teams).
 events=load('poly_euro_events')
 for g in sorted(load('statsbomb_matches'),key=lambda g:(g['match_date'],g['kick_off'],g['match_id']),reverse=True)[:20]:
  h=g['home_team']['home_team_name'];a=g['away_team']['away_team_name']
  candidates=[e for e in events if 'vs.' in e['title'] and all(norm(t) in norm(e['title']) for t in [h,a]) and (e['title'].startswith('Euro 2024:') or e['title']=='Euro Finals: Spain vs. England')]
  if len(candidates)!=1:
   manifests.append({'sport':'euro','id':str(g['match_id']),'name':h+' vs '+a,'match_error':f'{len(candidates)} Polymarket events'});continue
  e=candidates[0];sb_start=epoch(g['match_date']+'T'+g['kick_off']+'+00:00');prefix='euro_'+str(g['match_id'])
  schedule=load('euro_schedule_'+g['match_date'].replace('-',''))['events']
  scheduled=[s for s in schedule if all(any(norm(t)==norm(c['team']['displayName']) for c in s['competitions'][0]['competitors']) for t in [h,a])]
  if len(scheduled)!=1:raise ValueError('Ambiguous independent Euro schedule: '+h+' vs '+a)
  start=epoch(scheduled[0]['date'])
  prices_file=prefix+('_prices_corrected' if start!=sb_start else '_prices')
  # Choose first non-draw winner contract; retain description and outcome mapping for audit.
  markets=[m for m in e['markets'] if any(norm(t) in norm(m['question']) for t in [h,a])]
  m=markets[0] if markets else e['markets'][0]
  token=json.loads(m['clobTokenIds'])[0]
  manifests.append({'sport':'euro','id':str(g['match_id']),'name':h+' vs '+a,'scheduled_start':datetime.datetime.fromtimestamp(start,datetime.timezone.utc).isoformat(),'market':m,'poly_event':e['slug'],'statsbomb_match':g,'prices_file':prices_file,'schedule_event_id':scheduled[0]['id'],'mapping_note':f'Teams matched; ESPN scheduled UTC minus StatsBomb kickoff interpreted as UTC: {start-sb_start} seconds.'})
  urls[prefix+'_events']=f"https://raw.githubusercontent.com/statsbomb/open-data/master/data/events/{g['match_id']}.json"
  urls[prices_file]=f'https://clob.polymarket.com/prices-history?market={token}&startTs={start}&endTs={start+14400}&fidelity=1'
 (RAW/'audit_manifest.json').write_text(json.dumps(manifests,indent=2))
 print('manifest',len(manifests),'requests',len(urls), 'mapping_errors',[(m['sport'],m.get('name',m['market']['title'] if 'market'in m else ''),m['match_error']) for m in manifests if 'match_error'in m],flush=True)
 for i,(n,d) in enumerate(concurrent.futures.ThreadPoolExecutor(5).map(lambda x:fetch(*x),urls.items())):
  if isinstance(d,dict) and 'error'in d:print(n,d['error'],flush=True)
  elif i%10==0:print('downloaded',i+1,n,flush=True)

if __name__=='__main__':collect()
