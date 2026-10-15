"""Deterministic features for the pilot's supported contract families.

These parsers use market fields, never reference labels or model outputs.
"""
from datetime import datetime, timezone
from decimal import Decimal
from difflib import SequenceMatcher
import re
from zoneinfo import ZoneInfo

MONTHS = {m: i for i, m in enumerate(('jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'),1)}
ET = ZoneInfo('America/New_York')


def period(text):
    match=re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})',text,re.I)
    return f'{match[2]}-{MONTHS[match[1][:3].lower()]:02d}' if match else None


def code_date(text):
    match=re.search(r'-(\d{2})([A-Z]{3})(\d{2})',text)
    return f'20{match[1]}-{MONTHS[match[2].lower()]:02d}-{match[3]}' if match else None


def dollar(text):
    match=re.search(r'\$\s*([\d,]+(?:\.\d+)?)',text)
    return str(Decimal(match[1].replace(',','')).normalize()) if match else None


def textnorm(text):
    text=text.lower().replace('u.s.','us').replace('united states','us').replace('federal reserve','fed')
    return ' '.join(re.findall(r'[a-z0-9]+',text))


def features(market):
    venue=market['venue'];raw=market['raw'];title=market['title'];rules=market['rules']
    result={'domain':'unsupported','key':None}
    if venue=='kalshi':
        ticker=raw['ticker'];series=ticker.split('-')[0]
        sports={'KXNFLGAME':'nfl','KXNBAGAME':'nba','KXNHLGAME':'nhl'}
        if series in sports:
            match=re.search(r'-(\d{2}[A-Z]{3}\d{2})([A-Z]+)-([A-Z]+)$',ticker)
            if match:
                result={'domain':sports[series], 'date':code_date(ticker),'teams_compact':match[2].lower(),'selected':match[3].lower(),'bet_type':'moneyline'}
                result['key']=(result['domain'],result['date'],result['teams_compact'],result['selected'],'moneyline')
        elif series=='KXFEDDECISION':
            match=re.search(r'-(\d{2})([A-Z]{3})-([CH])(\d+)$',ticker)
            if match:
                action='hold' if match[4]=='0' else ('cut' if match[3]=='C' else 'hike')
                bucket='large' if match[4]=='26' else ('25' if match[4]=='25' else '0')
                result={'domain':'fed_decision','period':f'20{match[1]}-{MONTHS[match[2].lower()]:02d}','action':action,'bucket':bucket}
                result['key']=(result['domain'],result['period'],action,bucket)
        elif series in ('KXFED','KXFEDFUNDSYEAR'):
            result={'domain':'fed_level','key':None}
        elif series=='KXBTCMAXMON':
            result={'domain':'btc_touch','period':period(title),'threshold':dollar(title)}
            result['key']=(result['domain'],result['period'],result['threshold'])
        elif series=='KXBTCD':
            match=re.search(r'-(\d{2}[A-Z]{3}\d{2})(\d{2})-',ticker)
            if match:
                instant=datetime.fromisoformat(code_date(ticker)+f'T{match[2]}:00:00').replace(tzinfo=ET).astimezone(timezone.utc).isoformat()
                result={'domain':'btc_point','instant_utc':instant,'threshold':dollar(raw.get('yes_sub_title',''))}
                result['key']=(result['domain'],instant,result['threshold'])
        elif series in ('KXCPIYOY','KXCPICOREYOY','KXCPI'):
            domain={'KXCPIYOY':'headline_yoy','KXCPICOREYOY':'core_yoy','KXCPI':'headline_mom'}[series]
            strike=raw.get('floor_strike')
            lower=str((Decimal(str(strike))+Decimal('0.1')).normalize()) if strike is not None else None
            result={'domain':domain,'period':period(title),'bucket':['ge',lower],'publication_precision':'0.1 percent'}
            result['key']=(domain,result['period'],'ge',lower)
        elif series=='KXALIENS':result={'domain':'aliens','key':None}
    else:
        slug=raw['slug'];match=re.match(r'^(nba|nfl|nhl)-([a-z]+)-([a-z]+)-(\d{4}-\d{2}-\d{2})',slug)
        if match:
            league=match[1];aliases={'nfl':{'la':'lar'},'nba':{},'nhl':{'cal':'cgy','mon':'mtl','las':'vgk','lak':'la','utah':'uta'}}[league]
            away=aliases.get(match[2],match[2]);home=aliases.get(match[3],match[3]);game_date=match[4]
            if raw.get('gameStartTime'):
                game_date=datetime.fromisoformat(raw['gameStartTime'].replace('Z','+00:00')).astimezone(ET).date().isoformat()
            kind=raw.get('sportsMarketType','unknown')
            outcome=market['selected_outcome']
            outcomes=market['outcomes']
            selected=away if kind=='moneyline' and outcome==outcomes[0] else home if kind=='moneyline' and outcome==outcomes[1] else None
            result={'domain':league,'date':game_date,'teams_compact':away+home,'selected':selected,'bet_type':kind}
            result['key']=(league,game_date,away+home,selected,kind)
        elif 'fed' in title.lower() and 'meeting' in title.lower():
            action='hold' if 'no change' in title.lower() else 'cut' if 'decrease' in title.lower() else 'hike' if 'increase' in title.lower() else None
            bucket='0' if action=='hold' else 'large' if '50+' in title else '25' if '25 bps' in title else None
            result={'domain':'fed_decision','period':period(title),'action':action,'bucket':bucket}
            result['key']=(result['domain'],result['period'],action,bucket)
        elif 'target federal funds rate' in title.lower():result={'domain':'fed_level','key':None}
        elif 'bitcoin' in title.lower() and 'reach' in title.lower() and 'in ' in title.lower():
            result={'domain':'btc_touch','period':period(title+' '+slug.replace('-',' ')),'threshold':dollar(title)}
            result['key']=(result['domain'],result['period'],result['threshold'])
        elif 'bitcoin' in title.lower() and 'above' in title.lower():
            match=re.search(r'(\d{4}-\d{2}-\d{2})',slug)
            if match and 'closing at 12:00' in rules:
                instant=datetime.fromisoformat(match[1]+'T12:00:00').replace(tzinfo=ET).astimezone(timezone.utc).isoformat()
                result={'domain':'btc_point','instant_utc':instant,'threshold':dollar(title)}
                result['key']=(result['domain'],instant,result['threshold'])
        elif 'annual inflation' in title.lower():
            match=re.search(r'([\d.]+)%',title)
            bucket='ge' if 'or more' in title else 'eq'
            threshold=str(Decimal(match[1]).normalize()) if match else None
            result={'domain':'headline_yoy','period':period(rules),'bucket':[bucket,threshold],'publication_precision':'0.1 percent'}
            result['key']=(result['domain'],result['period'],bucket,threshold)
        elif 'aliens exist' in title.lower():result={'domain':'aliens','key':None}
    # Incomplete extraction must abstain, never create exact matches from nulls.
    if result.get('key') and any(value is None for value in result['key']):result['key']=None
    return result


def identity(a,b):
    fa=features(a);fb=features(b)
    if fa['domain']!='unsupported' and fb['domain']!='unsupported' and fa['domain']!=fb['domain']:
        return 'nonmatch','different contract families'
    if fa.get('key') and fb.get('key'):
        return ('match','identical deterministic identity key') if fa['key']==fb['key'] else ('nonmatch','different deterministic identity keys')
    # Open-ended prose families are intentionally left for semantic review.
    return 'review','unsupported or incomplete deterministic identity extraction'


def settlement(a,b,identity_decision):
    if identity_decision=='nonmatch':return 'different','different advertised proposition or observation instant'
    if identity_decision!='match':return 'unknown','identity has not been established'
    ar=a['rules'].lower();br=b['rules'].lower();domain=features(a)['domain']
    if domain in ('nfl','nba','nhl') and '48 hours' in ar and 'fair price' in ar and '50-50' in br and 'until the game has been completed' in br:
        return 'different','48-hour/fair-price settlement versus waiting for completion and 50-50 cancellation'
    if domain.startswith('btc_') and 'cf ' in ar and 'binance' in br:
        return 'different','CF Benchmarks index/aggregation versus Binance BTC/USDT candle'
    if domain=='headline_yoy' and 'six months' in ar and 'most recent previous month' in br:
        return 'different','extended wait for target-month CPI versus fallback to previous-month CPI'
    if domain=='fed_decision' and features(a).get('bucket')=='25' and 'rounded up to the nearest 25' in br:
        return 'different','exact 25bps versus rounding a 12.5bps change into the 25bps bucket'
    return 'unknown','no complete deterministic compatibility proof or explicit supported counterexample'


def retrieval_score(a,b):
    fa=features(a);fb=features(b)
    score=SequenceMatcher(None,textnorm(a['title']),textnorm(b['title'])).ratio()
    if fa['domain']==fb['domain'] and fa['domain']!='unsupported':score+=2
    if fa.get('key') and fb.get('key') and fa['key']==fb['key']:score+=10
    for field in ('period','date','teams_compact','threshold','instant_utc','action','bucket','selected','bet_type'):
        if fa.get(field) is not None and fa.get(field)==fb.get(field):score+=0.5
    return score
