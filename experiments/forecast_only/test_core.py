import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.trade_env.config import make_config
from experiments.forecast_only.core import action, brier, digest, guard, messages, parse, reward

class CoreTests(unittest.TestCase):
    def test_strict_probabilities(self):
        self.assertEqual(parse('{"probs":{"home":0.4,"draw":0.3,"away":0.3}}')['draw'],.3)
        for text in ['null','[]','{"probs":null}','{"probs":{"home":true,"draw":0,"away":0}}',
            '{"probs":{"home":NaN,"draw":0,"away":0}}','{"probs":{"home":1,"home":0,"draw":0,"away":0}}',
            '{"probs":{"home":0.4,"draw":0.3,"away":0.2}}','{"probs":{"home":1,"draw":0,"away":0},"action":"buy_home"}']:
            with self.subTest(text=text): self.assertIsNone(parse(text))
    def test_reward_is_proper_and_match_normalized(self):
        belief={'home':.2,'draw':.5,'away':.3}
        truthful=sum(belief[o]*reward(belief,o,1) for o in belief)
        for candidate in [{'home':1,'draw':0,'away':0},{'home':.1,'draw':.8,'away':.1}]:
            self.assertGreater(truthful,sum(belief[o]*reward(candidate,o,1) for o in belief))
        self.assertAlmostEqual(sum(reward(belief,'draw',5) for _ in range(5)),-brier(belief,'draw'))
        self.assertEqual(reward(None,'draw',5),-.4)
    def test_fixed_execution(self):
        cfg=make_config();prices={'home':.4,'draw':.3,'away':.3}
        self.assertEqual(action(prices,prices,'flat',cfg),'do_nothing')
        self.assertEqual(action({'home':.7,'draw':.2,'away':.1},prices,'flat',cfg),'buy_home')
        self.assertEqual(action({'home':0,'draw':0,'away':1},prices,'home',cfg),'keep')
        self.assertEqual(action(None,prices,'flat',cfg),'do_nothing')
    def test_prompt_excludes_labels_and_execution(self):
        task={'messages':[{}, {'content':'Clock: Halftime\nPrices\nBuying now costs about: secret\nPosition: none'}],
              'scoring':{'result':'home','fill_prices':{'home':.9}}}
        msg=messages(task);self.assertNotIn('secret',str(msg));self.assertNotIn('.9',str(msg));self.assertNotIn('Position:',str(msg))
        task['scoring']={'result':'away','fill_prices':{'home':.1}}
        self.assertEqual(messages(task),msg)
    def test_guard_rejects_overlap_tampering_and_missing(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'ledger.sqlite3'
            with self.assertRaises(ValueError):guard(p,['x'])
            db=sqlite3.connect(p);db.execute('CREATE TABLE events(seq INTEGER,body TEXT,sha256 TEXT)')
            first={'kind':'policy','previous':'0'*64,'payload':{}};second={'kind':'cohort','previous':digest(first),'payload':{'matches':[{'match_id':'sealed'}]}}
            for i,e in enumerate([first,second],1):db.execute('INSERT INTO events VALUES(?,?,?)',(i,json.dumps(e),digest(e)))
            db.commit();self.assertEqual(guard(p,['dev'])['protected_matches'],1)
            with self.assertRaises(ValueError):guard(p,['sealed'])
            db.execute('UPDATE events SET sha256=? WHERE seq=2',('wrong',));db.commit()
            with self.assertRaises(ValueError):guard(p,['dev'])
            db.close()

if __name__=='__main__':unittest.main()
