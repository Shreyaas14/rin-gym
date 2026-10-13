"""Bounded native Tinker cookbook pilot; run without --live for preflight only."""
import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics

import chz
import numpy as np
import tinker
from tinker_cookbook import checkpoint_utils, renderers
from tinker_cookbook.rl import train, types as rl
from tinker_cookbook.tokenizer_utils import get_tokenizer
from app.trade_env.tasks import read_tasks
from app.trade_env.replay import by_match, Replay
from app.trade_env.train_tinker import TradeDataset, read_answer
from experiments.forecast_only.core import messages, parse, brier, reward, action, guard, digest


def append(path, row):
    with Path(path).open('a') as f:
        f.write(json.dumps(row,allow_nan=False)+'\n');f.flush()


class ForecastEnv(rl.Env):
    def __init__(self,tasks,renderer):
        self.tasks,self.renderer,self.i=tasks,renderer,0
    async def initial_observation(self):
        return self.renderer.build_generation_prompt(messages(self.tasks[self.i])), self.renderer.get_stop_sequences()
    async def step(self, tokens, *, extra=None):
        task=self.tasks[self.i];text,truncated=read_answer(self.renderer,tokens)
        p=None if truncated else parse(text)
        value=reward(p,task['scoring']['result'],len(self.tasks))
        self.i+=1;done=self.i==len(self.tasks)
        obs,stop=(tinker.ModelInput.empty(),self.renderer.get_stop_sequences()) if done else await self.initial_observation()
        return rl.StepResult(reward=value,episode_done=done,next_observation=obs,next_stop_condition=stop,
            metrics={'brier':brier(p,task['scoring']['result']),'valid':float(p is not None)},
            logs={'task_id':task['id'],'completion':text})


@dataclass(frozen=True)
class Group(rl.EnvGroupBuilder):
    tasks:list
    renderer:object
    size:int
    async def make_envs(self):return [ForecastEnv(self.tasks,self.renderer) for _ in range(self.size)]
    def logging_tags(self):return ['train']


@chz.chz
class Builder(rl.RLDatasetBuilder):
    path:str
    model:str
    renderer_name:str
    group_size:int
    groups_per_batch:int
    seed:int
    async def __call__(self):
        p=json.loads((Path(self.path)/'protocol.json').read_text())
        ts=read_tasks(Path(self.path)/'tasks.jsonl.gz')
        guard(p['ledger'],[t['match_id'] for t in ts])
        r=renderers.get_renderer(self.renderer_name,get_tokenizer(self.model))
        matches=list(by_match([t for t in ts if t['split']=='train' and t['position']=='flat']).values())
        return TradeDataset(matches,lambda m:Group(m,r,self.group_size),self.groups_per_batch,1,self.seed),None


def score_predictions(tasks, predictions, config):
    rows=[]
    for mid,ts in sorted(by_match(tasks).items()):
        flat=sorted([t for t in ts if t['position']=='flat'],key=lambda t:t['t'])
        ps=[predictions[t['id']] for t in flat]
        replay=Replay(ts,config)
        while (task:=replay.next_task()) is not None:
            p=predictions[f"{mid}:{task['decision']}:flat"]
            replay.step(action(p,task['scoring']['shown_prices'],replay.position,config))
        result=replay.result()
        rows.append({'match_id':mid,'brier':statistics.mean(brier(p,t['scoring']['result']) for p,t in zip(ps,flat)),
            'log_loss':statistics.mean(-math.log(max(1e-6,p[t['scoring']['result']])) if p else -math.log(1e-6) for p,t in zip(ps,flat)),
            'valid_rate':sum(p is not None for p in ps)/len(ps),'pnl':result['pnl'],'cost':result['cost'],'trades':result['trades'],
            'steps':result['steps']})
    return rows


async def evaluate(client,label,path,tasks,renderer,p):
    guard(p['ledger'],[t['match_id'] for t in tasks])
    predictions={};sem=asyncio.Semaphore(8)
    async def one(task):
        async with sem:
            prompt=renderer.build_generation_prompt(messages(task))
            # Receipt before sending, with explicit retrospective context.
            append(path/'requests.jsonl',{'model':label,'task_id':task['id'],'created_utc':datetime.now(timezone.utc).isoformat(),
                'observation_sha256':digest(messages(task)),'protocol_sha256':p['protocol_sha256'],'kind':'retrospective_development'})
            response=await client.sample_async(prompt=prompt,num_samples=1,sampling_params=tinker.SamplingParams(
                max_tokens=p['max_tokens'],temperature=0,stop=renderer.get_stop_sequences(),seed=p['seed']))
            seq=response.sequences[0];text,truncated=read_answer(renderer,seq.tokens)
            probs=None if truncated else parse(text)
            predictions[task['id']]=probs
            append(path/'predictions.jsonl',{'model':label,'task_id':task['id'],'recorded_utc':datetime.now(timezone.utc).isoformat(),
                'probs':probs,'completion':text,'truncated':truncated,'input_tokens':prompt.length,'output_tokens':len(seq.tokens),
                'observation_sha256':digest(messages(task)),'kind':'retrospective_development'})
    await asyncio.gather(*(one(t) for t in tasks if t['position']=='flat'))
    return score_predictions(tasks,predictions,p['config'])


def summarize(all_rows):
    summary={}
    for label,rows in all_rows.items():
        summary[label]={k:statistics.mean(r[k] for r in rows) for k in ('brier','log_loss','valid_rate','pnl','cost','trades')}
    rng=np.random.default_rng(20261013);n=len(all_rows['base']);indices=rng.integers(0,n,(4000,n))
    deltas={}
    for left,right in [('final','base'),('final','market')]:
        assert [r['match_id'] for r in all_rows[left]]==[r['match_id'] for r in all_rows[right]]
        for metric in ('brier','pnl'):
            d=np.array([a[metric]-b[metric] for a,b in zip(all_rows[left],all_rows[right])])
            deltas[f'{left}_minus_{right}/{metric}']={'mean':float(d.mean()),'match_bootstrap_95':np.quantile(d[indices].mean(1),[.025,.975]).tolist()}
    return {'matches':n,'metrics':summary,'paired_deltas':deltas,'flat_policy_pnl':0,
       'interpretation':'Small historical development pilot. No holdout evaluation. Match bootstrap ignores shared-team dependence. Invalid forecasts receive worst Brier=2 and log loss=-log(1e-6).'}


async def main(args):
    path=Path(args.path);p=json.loads((path/'protocol.json').read_text());saved=p.pop('protocol_sha256');assert digest(p)==saved;p['protocol_sha256']=saved
    assert hashlib.sha256((path/'tasks.jsonl.gz').read_bytes()).hexdigest()==p['tasks_sha256']
    ts=read_tasks(path/'tasks.jsonl.gz');assert {t['split'] for t in ts}=={'train','val'}
    for s in ('train','val'):assert sorted({t['match_id'] for t in ts if t['split']==s})==p['cohorts'][s]
    assert not set(p['cohorts']['train']) & set(p['cohorts']['val'])
    before=guard(p['ledger'],[t['match_id'] for t in ts]);assert before==p['seal']
    renderer=renderers.get_renderer(p['renderer'],get_tokenizer(p['model']))
    flat=[t for t in ts if t['position']=='flat'];train_flat=[t for t in flat if t['split']=='train'];val=[t for t in ts if t['split']=='val']
    lens={t['id']:renderer.build_generation_prompt(messages(t)).length for t in flat}
    train_n=len(train_flat)*p['group_size'];eval_n=sum(t['position']=='flat' for t in val)*2
    train_prompt=sum(lens[t['id']] for t in train_flat)*p['group_size'];eval_prompt=sum(lens[t['id']] for t in flat if t['split']=='val')*2
    rates=p['prices_per_million'];max_out=(train_n+eval_n)*p['max_tokens']
    upper=((train_prompt+eval_prompt)*rates['prefill']+max_out*rates['sample']+(train_prompt+train_n*p['max_tokens'])*rates['train'])/1e6
    estimate={'upper_estimated_usd':upper,'budget_usd':p['budget_usd'],'train_completions':train_n,'eval_completions':eval_n,
       'max_output_tokens':max_out,'prompt_tokens':train_prompt+eval_prompt,'max_prompt':max(lens.values()),
       'pricing_source':p['pricing_source'],'note':'Token-rate estimate, not provider billing limit. No automatic experiment restart.'}
    if upper*2>p['budget_usd']:raise ValueError('Insufficient budget with 2x reserve')
    if max(lens.values())+p['max_tokens']>32768:raise ValueError('Context limit')
    (path/'estimate.json').write_text(json.dumps(estimate,indent=2));print(json.dumps(estimate),flush=True)
    if not args.live:return
    # Refuse automatic retries/resumes of partially paid runs.
    with (path/'STARTED.json').open('x') as f:json.dump({'protocol':saved,'utc':datetime.now(timezone.utc).isoformat()},f)
    service=tinker.ServiceClient()
    base=service.create_sampling_client(base_model=p['model'])
    rows={'base':await evaluate(base,'base',path,val,renderer,p)}
    (path/'base-matches.json').write_text(json.dumps(rows['base'],indent=2))
    print('Base evaluation complete',flush=True)
    cfg=train.Config(recipe_name='rin_forecast_only_pilot',model_name=p['model'],renderer_name=p['renderer'],lora_rank=p['lora_rank'],
        learning_rate=p['learning_rate'],max_tokens=p['max_tokens'],dataset_builder=Builder(path=str(path),model=p['model'],renderer_name=p['renderer'],
        group_size=p['group_size'],groups_per_batch=p['groups_per_batch'],seed=p['seed']),eval_every=0,save_every=0,
        remove_constant_reward_groups=False,num_substeps=1,temperature=1.0,loss_fn='importance_sampling',max_steps=p['max_steps'],log_path=str(path/'training'))
    await train.main(cfg)
    last=checkpoint_utils.get_last_checkpoint(cfg.log_path,'sampler_path')
    if not last:raise ValueError('No final checkpoint')
    (path/'checkpoint.json').write_text(json.dumps({'sampler_path':last.sampler_path,'batch':last.batch},indent=2))
    client=service.create_sampling_client(model_path=last.sampler_path)
    rows['final']=await evaluate(client,last.sampler_path,path,val,renderer,p)
    rows['market']=score_predictions(val,{t['id']:t['scoring']['market_probs'] for t in val if t['position']=='flat'},p['config'])
    result=summarize(rows);result['seal_after']=guard(p['ledger'],[t['match_id'] for t in ts]);assert result['seal_after']==before
    (path/'evaluation-matches.json').write_text(json.dumps(rows,indent=2));(path/'summary.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2),flush=True)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--path',default='artifacts/forecast-only');ap.add_argument('--live',action='store_true');args=ap.parse_args()
    asyncio.run(main(args))
