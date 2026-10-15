"""Build source-backed reference pairs before any model inference."""
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent


def catalogs():
    kalshi={};poly={}
    series={r['ticker']:r for r in json.loads((ROOT/'kalshi_series.json').read_text())['series']}
    for path in sorted((ROOT/'catalog').glob('kalshi_*.json')):
        for raw in json.loads(path.read_text())['markets']:
            st=series[raw['ticker'].split('-')[0]]
            name=Path(st['contract_terms_url']).stem
            terms=(ROOT/'sources'/(name+'.txt')).read_text()
            kalshi[raw['ticker']]={
                'venue':'kalshi','id':raw['ticker'],'title':raw['title'],
                'selected_outcome':'Yes','selected_outcome_description':raw.get('yes_sub_title',''),
                'outcomes':['Yes','No'],'rules':raw['rules_primary']+'\n\n'+raw.get('rules_secondary',''),
                'contract_terms_text':terms,'contract_terms_url':st['contract_terms_url'],
                'settlement_sources':st.get('settlement_sources',[]),
                'url':'https://external-api.kalshi.com/trade-api/v2/markets/'+raw['ticker'],
                'raw':raw,
            }
    for path in sorted((ROOT/'catalog').glob('polymarket_*.json')):
        for raw in json.loads(path.read_text()):
            outcomes=json.loads(raw['outcomes']) if isinstance(raw['outcomes'],str) else raw['outcomes']
            tokens=json.loads(raw.get('clobTokenIds','[]')) if isinstance(raw.get('clobTokenIds'),str) else raw.get('clobTokenIds',[])
            poly[raw['id']]={
                'venue':'polymarket','id':raw['id'],'title':raw['question'],
                'selected_outcome':outcomes[0],'outcomes':outcomes,'selected_token_id':tokens[0] if tokens else None,
                'rules':raw['description'],'resolution_source':raw.get('resolutionSource',''),
                'url':'https://polymarket.com/event/'+raw['events'][0]['slug'] if raw.get('events') else 'https://polymarket.com/market/'+raw['slug'],
                'raw':raw,
            }
    return kalshi,poly


def main():
    k,p=catalogs();specs=[]
    def add(kt,pi,identity,settlement,rationale,family):
        assert kt in k,kt
        assert str(pi) in p,pi
        specs.append({'id':f'pair_{len(specs)+1:03d}','family':family,'kalshi_id':kt,'polymarket_id':str(pi),'identity_gold':identity,'settlement_gold':settlement,'rationale':rationale})
    sports=[
        ('KXNFLGAME-26OCT15SEADEN-SEA','4212356','nfl'),
        ('KXNFLGAME-26OCT18ARILAR-ARI','4329890','nfl'),
        ('KXNFLGAME-26OCT18CARPHI-CAR','4329888','nfl'),
        ('KXNBAGAME-26OCT13NYKTOR-NYK','5394609','nba'),
        ('KXNBAGAME-26OCT13CLEORL-CLE','5394606','nba'),
        ('KXNBAGAME-26OCT13INDOKC-IND','5394612','nba'),
        ('KXNHLGAME-26OCT13STLOTT-STL','4616362','nhl'),
        ('KXNHLGAME-26OCT13FLACBJ-FLA','4616299','nhl'),
    ]
    for kt,pi,family in sports:
        add(kt,pi,'match','different','Same scheduled game and selected winning team, including overtime. A game postponed beyond 48 hours can settle at Kalshi fair price while Poly waits for completion; outright cancellation is Poly 50-50.','sports_'+family)
    sport_neg=[
        ('KXNFLGAME-26OCT15SEADEN-SEA','4367619','Winner versus covering a -2.5 spread.'),
        ('KXNBAGAME-26OCT13NYKTOR-NYK','5639889','Knicks winner versus Raptors covering -10.5.'),
        ('KXNBAGAME-26OCT13CLEORL-CLE','5635528','Winner versus Cavaliers covering -1.5.'),
        ('KXNBAGAME-26OCT13INDOKC-IND','5641755','Pacers winner versus Thunder covering -7.5.'),
        ('KXNHLGAME-26OCT13STLOTT-STL','5637475','Winner versus Blues covering -1.5.'),
        ('KXNHLGAME-26OCT13FLACBJ-FLA','4616303','Florida winner versus game total over 5.5.'),
        ('KXNFLGAME-26OCT25KCSEA-SEA','4212356','Same franchise but a different opponent and scheduled game date.'),
        ('KXNHLGAME-26OCT14DALCOL-COL','4616401','Same teams but games on consecutive dates; October 14 versus October 13 ET.'),
        ('KXNFLGAME-26OCT15SEADEN-DEN','4212356','Kalshi Denver YES versus explicitly selected Poly Seahawks outcome.'),
        ('KXNBAGAME-26OCT13NYKTOR-TOR','5394609','Kalshi Toronto YES versus explicitly selected Poly Knicks outcome.'),
        ('KXNHLGAME-26OCT13STLOTT-OTT','4616362','Kalshi Ottawa YES versus explicitly selected Poly Blues outcome.'),
        ('KXNBAGAME-26OCT13NYKTOR-NYK','4616428','Different sport, league, franchises and winning outcome.'),
    ]
    for kt,pi,reason in sport_neg:add(kt,pi,'nonmatch','different',reason,'sports_near_miss')
    fed={'26OCT':{'H0':'2589812','H25':'2589813','H26':'2589814','C25':'2589811','C26':'2589810'},'26DEC':{'H0':'3215007','H25':'3215008','H26':'3215009','C25':'3215006','C26':'3215005'}}
    for meeting,mapping in fed.items():
        for bucket,pi in mapping.items():
            reason='Same FOMC meeting and advertised rate-change bucket. '
            if bucket.endswith('25'):
                reason+='Kalshi requires an exact 25bps action; Poly explicitly rounds a 12.5bps change into the 25bps bracket, permitting different payouts.'
                compatible='different'
            else:
                reason+='The >25bps Kalshi bucket maps to the Poly 50+ bracket under its explicit 25bps upward rounding. No-change maps to no-change. Cancellation/no-statement timing and later-release contingencies are not fully equivalent on the available evidence; strict compatibility remains unresolved.'
                compatible='unknown'
            add('KXFEDDECISION-'+meeting+'-'+bucket,pi,'match',compatible,reason,'fed_'+meeting)
    for kt,pi,reason in [
        ('KXFEDDECISION-26OCT-C25','3215006','Different FOMC meeting: October versus December.'),
        ('KXFEDDECISION-26OCT-C25','2589813','Cut versus hike in the same meeting.'),
        ('KXFEDDECISION-26OCT-H0','2589810','No change versus large cut.'),
        ('KXFEDDECISION-26DEC-H0','2589812','December versus October meeting.'),
        ('KXFEDDECISION-27JAN-C25','3215006','January 2027 versus December 2026 meeting.'),
        ('KXFED-27APR-T4.00','2589812','Absolute rate above 4% after April 2027 versus zero rate change in October 2026.'),
    ]:add(kt,pi,'nonmatch','different',reason,'fed_near_miss')
    for strike,pi in [('9000000','5170734'),('9250000','5170733'),('10000000','5170730'),('10250000','5170729'),('8750000','5170735')]:
        add('KXBTCMAXMON-BTC-26OCT31-'+strike,pi,'match','different','Same advertised BTC monthly upper target. Kalshi uses a cumulative trimmed mean of CF BRTI from issuance, strictly above the strike; Poly uses a Binance BTC/USDT one-minute high greater than or equal to the strike over the calendar month. A transient Binance high can pay Poly YES while the Kalshi trimmed mean remains below target.','bitcoin_monthly')
    for kt,pi,reason in [
        ('KXBTCMAXMON-BTC-26OCT31-10000000','5170734','Different target: $100,000 versus $90,000.'),
        ('KXBTCMAXMON-BTC-26OCT31-9750000','5170728','Different target: $97,500 versus $105,000.'),
        ('KXBTCMAXMON-BTC-26OCT31-10000000','5407354','Monthly touch target versus one daily observation, also different strike.'),
        ('KXBTCD-26OCT1417-T89999.99','5407354','Same displayed $90,000 target and calendar day, but 5PM EDT CF average versus noon ET Binance close; boundary and source also differ.'),
        ('KXBTCD-26OCT1320-T80499.99','5407354','Different day, observation time and advertised threshold.'),
    ]:add(kt,pi,'nonmatch','different',reason,'bitcoin_near_miss')
    add('KXCPIYOY-26SEP-T3.9','4469403','match','different','At explicitly stated one-decimal BLS precision, above 3.9% equals 4.0% or more for September headline YoY CPI. If shutdown delays the release past the next report, Poly can use the previous month while Kalshi extends its wait up to six months after the shutdown.','cpi')
    for kt,pi,reason in [
        ('KXCPIYOY-26SEP-T4.0','4469403','At exactly 4.0%, Kalshi strictly above 4.0 pays NO, Poly 4.0 or more pays YES.'),
        ('KXCPICOREYOY-26NOV-T3.0','4469403','Core versus headline CPI and a different reference month.'),
        ('KXCPI-26SEP-T0.3','4469401','Monthly headline inflation above 0.3% versus annual headline inflation exactly 3.9%.'),
    ]:add(kt,pi,'nonmatch','different',reason,'cpi_near_miss')
    for kt,pi in [('KXALIENS-27','703257'),('KXALIENS-26NOV','5180509')]:
        add(kt,pi,'match','unknown','Same advertised US official confirmation of extraterrestrial life or technology and same end-date window. Kalshi enumerates cabinet-level roles; Poly has a generic Cabinet definition and credible-reporting source. Full source/role/fallback equivalence has not been established.','aliens')
    for kt,pi,reason in [
        ('KXALIENS-26DEC','703257','Before December 1 versus before January 1.'),
        ('KXALIENS-27-28','703257','Before January 1, 2028 versus before January 1, 2027.'),
    ]:add(kt,pi,'nonmatch','different',reason,'aliens_near_miss')
    for kt,pi in [
        ('KXNFLGAME-26OCT15SEADEN-SEA','2589812'),
        ('KXBTCMAXMON-BTC-26OCT31-10000000','703257'),
        ('KXCPIYOY-26SEP-T3.9','4616362'),
        ('KXALIENS-27','5170730'),
        ('KXFEDDECISION-26OCT-C25','4616299'),
        ('KXNHLGAME-26OCT13STLOTT-STL','5407354'),
    ]:add(kt,pi,'nonmatch','different','Distinct underlying event, category, and selected payout predicate; the outcomes can vary independently.','unrelated_control')
    assert len(specs)==60,len(specs)
    assert len({(r['kalshi_id'],r['polymarket_id']) for r in specs})==60
    references=[]
    for row in specs:
        a=k[row['kalshi_id']];b=p[row['polymarket_id']]
        references.append({**row,'a':a,'b':b})
    (ROOT/'reference.json').write_text(json.dumps(references,indent=2)+'\n')
    (ROOT/'markets.json').write_text(json.dumps({'kalshi':list(k.values()),'polymarket':list(p.values())},indent=2)+'\n')
    freeze={
        'frozen_at_utc':datetime.now(timezone.utc).isoformat(),
        'labeler':'investigating assistant, source-based, before inference; no independent adjudication',
        'pairs':len(references),'identity_counts':dict(Counter(r['identity_gold'] for r in references)),
        'settlement_counts':dict(Counter(r['settlement_gold'] for r in references)),
        'kalshi_markets':len(k),'polymarket_markets':len(p),
        'file_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('reference.json','features.py','PROTOCOL.md','markets.json')},
    }
    (ROOT/'freeze.json').write_text(json.dumps(freeze,indent=2)+'\n')
    print(json.dumps(freeze,indent=2))


if __name__=='__main__':main()
