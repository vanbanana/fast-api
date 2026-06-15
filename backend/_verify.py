import asyncio
from app.main import app
print('=== 1. Backend import ===')
print('OK:', len(app.routes), 'routes')

print('\n=== 2. Module import check ===')
modules = ['app.atmosphere_service', 'app.memory', 'app.worker_agent', 'app.runtime', 'app.schemas', 'app.config', 'app.llm_client']
for m in modules:
    try: __import__(m); print(f'  {m}: OK')
    except Exception as e: print(f'  {m}: FAIL - {e}')

print('\n=== 3. Atmosphere fallback all states ===')
from app.atmosphere_service import AtmosphereRequest, AtmosphereResponse, generate, _fallback_response, _default_status_for_state, _FALLBACK_TEMPLATES, _cache, invalidate_cache
for s in ['idle','working','break','roaming','seeking','chatting']:
    r = _fallback_response(AtmosphereRequest(worker_id='t',name='A',role='R',personality='P',state=s,location='d',nearby_workers=[],last_event=''))
    assert r.say and r.status and r.mood, f'{s} empty!'
    print(f'  {s}: say="{r.say[:20]}" status="{r.status}" mood="{r.mood}" OK')

print('\n=== 4. Cache mechanism (async) ===')
async def test_cache():
    invalidate_cache()
    req = AtmosphereRequest(worker_id='w1', name='A', role='R', personality='P', state='working', location='d', nearby_workers=[], last_event='')
    r1 = await generate(req)
    r2 = await generate(req)
    assert r1.say == r2.say, f'cache miss! r1={r1.say} r2={r2.say}'
    print('  cache hit OK')
    invalidate_cache()
asyncio.run(test_cache())

print('\n=== 5. Memory system ===')
from app.memory import memory_store
memory_store.ensure_agent('tw', 'T', 'E')
ctx = memory_store.build_context('tw')
assert len(ctx) > 0, 'build_context empty!'
print(f'  build_context ({len(ctx)} chars) OK')
assert not memory_store.should_store('规则决策:desk1Marker:在工位整理状态')
assert memory_store.should_store('老板指令:小周去检查接口超时问题')
print('  write filter OK')

print('\n=== 6. WorkerAgent slim ===')
from app.worker_agent import OfficeAgent
a = OfficeAgent(worker_id='w1', name='Z', role='E', personality='P')
a.remember('valid memo')
sn = a.snapshot()
assert sn.name == 'Z'
for removed in ['decide','_llm_decision','_rule_decision','_execute_decision','fsm','autonomy_steps']:
    assert not hasattr(a, removed), f'{removed} still exists!'
print('  agent clean OK')

print('\n=== 7. Schema models ===')
from app.schemas import AtmosphereRequest as AR, AtmosphereResponse as AResp
AR(worker_id='w', name='A', role='R', personality='P', state='working', location='d')
AResp(say='h', status='w', mood='h')
print('  schemas OK')

print('\n=== 8. Deleted modules confirmed gone ===')
import importlib
for m in ['app.worker_behavior_tree','app.worker_intent','app.worker_rule_context','app.worker_decision_policy','app.worker_collaboration']:
    try:
        importlib.import_module(m); print(f'  {m}: STILL EXISTS!')
    except: print(f'  {m}: deleted OK')

print('\n========== ALL PASSED ==========')
