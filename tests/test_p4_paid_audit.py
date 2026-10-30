"""P4 offline only: mock transport, ledger policy and existing workspace fixture."""
import asyncio
import json
import runpy
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from src.harness.factory import load_config, build_agent
from src.llm.client import LLMClient, LLMResponse, ToolCall, normalize_usage
from src.policy.model_budget import ModelBudgetLedger, BudgetLedgerError
from src.runtime.retry import RetryPolicy, RetryConfig, PaidCallStopped, AttemptSink
from src.runtime.session import RunSession, runtime_fingerprint

GOOD = dict(prompt_tokens=11, completion_tokens=3, total_tokens=14)


def test_audit_mode_changes_runtime_fingerprint_without_changing_legacy_baseline(tmp_path, monkeypatch):
    monkeypatch.setenv('LLM_API_KEY', 'synthetic-key')
    config = load_config('configs/sprint/b0-v2.yaml')
    agent = build_agent(config, tmp_path)
    legacy = runtime_fingerprint(agent)
    agent._retry_config.stop_on_unknown_usage = True
    assert runtime_fingerprint(agent) != legacy
    agent._retry_config.stop_on_unknown_usage = False
    assert runtime_fingerprint(agent) == legacy


def body(*, usage=GOOD, finish='stop', tools=False):
    message = {'content':'done'}
    if tools:
        message = {'content':None,'tool_calls':[{'id':'call-1','type':'function',
            'function':{'name':'list_dir','arguments':'{}'}}]}
    return dict(id='response-123', model='actual-served-model',system_fingerprint='fp_actual',
                usage=usage,choices=[{'message':message,'finish_reason':finish}])


def ledger(**changes):
    return ModelBudgetLedger(**(dict(max_model_calls=4,max_tokens=10000,max_cost_usd=1,
        price_table={'prompt_per_1k':0.0003,'completion_per_1k':0.0012}) | changes))


@pytest.mark.asyncio
@pytest.mark.parametrize('streaming', [False, True])
async def test_raw_usage_must_be_complete_without_normalization_or_stale_stream_fallback(streaming):
    async def handle(request):
        if not streaming:
            return httpx.Response(200, json=body(usage=GOOD | {'prompt_tokens': 11.0}))
        chunks = [{'choices': [{'delta': {'content': 'done'}, 'finish_reason': 'stop'}], 'usage': GOOD},
                  {'choices': [], 'usage': None}]
        wire = ''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n'
        return httpx.Response(200, content=wire.encode())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = LLMClient('https://offline.invalid/v1', '', 'requested', audit_metadata=True, http_client=http)
        async def call(messages, tools):
            if not streaming:
                return await client.chat(messages, tools)
            events = [event async for event in client.stream_chat(messages, tools)]
            return events[-1].data['response']
        budget = ledger()
        policy = RetryPolicy(RetryConfig(stop_on_unknown_usage=True), model_ledger=budget)
        with pytest.raises(PaidCallStopped, match='billing_usage_unknown'):
            await policy.execute(call, [], [], max_output_tokens=32)
    assert budget.calls_started == 1
    assert budget.tokens_committed == policy.sink.attempts[0].estimated_input_tokens + 32
    assert policy.sink.attempts[0].usage_provenance == 'unknown'


@pytest.mark.asyncio
async def test_nonstream_observed_metadata_full_usage_and_no_credentials():
    raw=body(usage=GOOD|{'prompt_cache_hit_tokens':2,'extra_details':{'other':7},'api_key':'synthetic-sensitive'})
    async def handle(request):
        assert json.loads(request.content)['thinking']=={'type':'disabled'}
        return httpx.Response(200,json=raw,headers={'x-request-id':'provider-request-42','authorization':'not-collected'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client=LLMClient('https://offline.invalid/v1','synthetic-key','requested-alias',thinking='disabled',
                         audit_metadata=True,http_client=http)
        result=await client.chat([{'role':'user','content':'hello'}])
    audit=result.provider_metadata
    assert audit['request_model']=='requested-alias' and audit['response_model']=='actual-served-model'
    assert audit['provider_request_id']=='provider-request-42' and audit['system_fingerprint']=='fp_actual'
    assert audit['response_id']=='response-123' and audit['finish_reason']=='stop'
    assert audit['started_at'] and audit['finished_at']
    assert audit['raw_usage']['extra_details']=={'other':7}
    assert audit['raw_usage']['api_key']=='[REDACTED]'
    assert 'synthetic-sensitive' not in json.dumps(audit) and 'not-collected' not in json.dumps(audit)


@pytest.mark.asyncio
async def test_stream_metadata_usage_and_continuation_with_mock_transport():
    seen=[]
    async def handle(request):
        payload=json.loads(request.content);seen.append(payload)
        if len(seen)==1:
            delta={'tool_calls':[{'index':0,'id':'call-1','type':'function',
                    'function':{'name':'list_dir','arguments':'{}'}}]};finish='tool_calls'
        else: delta={'content':'done'};finish='stop'
        chunks=[{'id':'stream-123','model':'served-stream','system_fingerprint':'fp_sse',
                 'choices':[{'delta':delta,'finish_reason':finish}]},
                {'choices':[],'usage':GOOD|{'additional':{'billable':14}}}]
        wire=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n'
        return httpx.Response(200,content=wire.replace('\\n','\n').encode(),headers={'x-request-id':'sse-request'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client=LLMClient('https://offline.invalid/v1','','requested',audit_metadata=True,http_client=http,thinking='disabled')
        async def collect(messages,tools):
            events=[event async for event in client.stream_chat(messages,tools)]
            assert events[-1].event=='llm_stream_done'
            return events[-1].data['response']
        # Use the actual client method binding for normal retry evidence collection.
        policy=RetryPolicy(RetryConfig(stop_on_unknown_usage=True),model_ledger=ledger())
        first=await policy.execute(collect,[{'role':'user','content':'list'}],[],max_output_tokens=64)
        assert first.tool_calls[0].arguments=={}
        messages=[{'role':'user','content':'list'}, {'role':'assistant','content':None,'tool_calls':[
            {'id':'call-1','type':'function','function':{'name':'list_dir','arguments':'{}'}}]},
            {'role':'tool','tool_call_id':'call-1','content':'files'}]
        second=await policy.execute(collect,messages,[],max_output_tokens=64)
    assert second.text=='done' and len(seen)==2
    assert first.provider_metadata['response_model']=='served-stream'
    assert second.provider_metadata['provider_request_id']=='sse-request'
    assert second.provider_metadata['raw_usage']['additional']=={'billable':14}
    assert policy.model_ledger.calls_started==2


@pytest.mark.asyncio
@pytest.mark.parametrize('usage',[None,{}, {'total_tokens':14}, GOOD|{'total_tokens':99},
    GOOD|{'prompt_tokens':-1},GOOD|{'completion_tokens':True}])
async def test_untrusted_usage_retains_reserve_and_blocks_current_and_restored_policy(usage):
    budget=ledger();policy=RetryPolicy(RetryConfig(max_retries=2,stop_on_unknown_usage=True),model_ledger=budget)
    calls=[]
    async def fn(*args):
        calls.append(True)
        return LLMResponse(text='x',usage=normalize_usage(usage),finish_reason='stop')
    with pytest.raises(PaidCallStopped,match='billing_usage_unknown'):
        await policy.execute(fn,[],[],max_output_tokens=32)
    reserved=policy.sink.attempts[0].estimated_input_tokens+32
    assert budget.tokens_committed==reserved and budget.calls_started==1
    assert budget.settlements[0].provenance=='unknown'
    with pytest.raises(PaidCallStopped): await policy.execute(fn,[],[],max_output_tokens=32)
    restored=RetryPolicy(RetryConfig(stop_on_unknown_usage=True),sink=AttemptSink(list(policy.sink.attempts)),model_ledger=budget)
    with pytest.raises(PaidCallStopped): await restored.execute(fn,[],[],max_output_tokens=32)
    assert calls==[True] and budget.calls_started==1


@pytest.mark.asyncio
@pytest.mark.parametrize('exception',[httpx.ReadTimeout('synthetic error'),RuntimeError('synthetic error'),asyncio.CancelledError()])
async def test_failed_or_interrupted_attempt_never_retries_or_allows_later_call(exception):
    policy=RetryPolicy(RetryConfig(stop_on_unknown_usage=True,max_retries=2),model_ledger=ledger());calls=[]
    async def fn(*args): calls.append(True);raise exception
    with pytest.raises((PaidCallStopped,asyncio.CancelledError)):
        await policy.execute(fn,[],[],max_output_tokens=32)
    with pytest.raises(PaidCallStopped): await policy.execute(fn,[],[],max_output_tokens=32)
    assert calls==[True] and policy.model_ledger.calls_started==1
    assert policy.model_ledger.tokens_committed>0


@pytest.mark.asyncio
async def test_length_with_complete_usage_is_billed_but_never_returned_for_tool_execution():
    budget=ledger();policy=RetryPolicy(RetryConfig(stop_on_unknown_usage=True),model_ledger=budget)
    async def fn(*args):
        return LLMResponse(tool_calls=[ToolCall('write','write_file',{'path':'x','content':'x'})],
            finish_reason='length',usage=normalize_usage(GOOD))
    with pytest.raises(PaidCallStopped,match='response_incomplete'): await policy.execute(fn,[],[],max_output_tokens=64)
    assert budget.tokens_committed==14 and policy.sink.attempts[0].billing_stop_reason=='response_incomplete'


@pytest.mark.asyncio
async def test_legacy_unknown_usage_still_returns_and_can_continue():
    policy=RetryPolicy(RetryConfig(),model_ledger=ledger())
    async def fn(*args): return LLMResponse(text='legacy',usage=normalize_usage(None))
    assert (await policy.execute(fn,[],[],max_output_tokens=32)).text=='legacy'
    assert (await policy.execute(fn,[],[],max_output_tokens=32)).text=='legacy'
    assert policy.model_ledger.calls_started==2


@pytest.mark.asyncio
@pytest.mark.parametrize('limit',[{'max_tokens':1},{'max_cost_usd':0.00000001},{'max_model_calls':0}])
async def test_budget_boundary_rejects_before_any_request(limit):
    policy=RetryPolicy(RetryConfig(stop_on_unknown_usage=True),model_ledger=ledger(**limit));calls=[]
    async def fn(*args): calls.append(True)
    with pytest.raises(BudgetLedgerError): await policy.execute(fn,[],[],max_output_tokens=32)
    assert calls==[] and policy.model_ledger.calls_started==0


def test_p4_config_is_opt_in_and_original_workspace_default_unchanged(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "synthetic-test-only")
    old=load_config('configs/workspace/sphinx-bm25.yaml');new=load_config('configs/workspace/p4-sphinx-deepseek.yaml')
    assert not old['retry']['stop_on_unknown_usage'] and new['retry']['stop_on_unknown_usage']
    assert new['llm']['thinking']=='disabled' and new['llm']['max_tokens']==4096
    assert new['runtime']['budget']['max_cost_usd']==0.6 and new['retry']['max_retries']==0
    assert new['runtime']['context']['keep_recent_tool_results']==4
    assert new['workspace']['finalization_reserve_seconds']==182


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['usage','length'])
async def test_opt_in_stop_reaches_existing_workspace_finalizer_without_executing_tools(tmp_path,failure):
    h=runpy.run_path(str(Path(__file__).with_name('test_workspace_completion.py')))
    loop,session,model,profile,executor=h['setup'](tmp_path,[])
    loop._retry_config.stop_on_unknown_usage=True
    async def chat(messages,tools):
        model.calls+=1
        return LLMResponse(tool_calls=h['write'](h['GOOD']),finish_reason='length' if failure=='length' else 'tool_calls',
            usage=normalize_usage(GOOD) if failure=='length' else normalize_usage(None))
    model.chat=chat
    with session:
        result=await session.run(loop,'public fixture task')
    state=RunSession.replay(session.store.directory)
    assert model.calls==1 and profile.transport.commands==0 and profile.transport.closed
    assert result.termination_kind==('response_incomplete' if failure=='length' else 'billing_usage_unknown')
    assert state.terminal is not None and state.attempts[-1]['billing_stop_reason']==result.termination_kind
    assert state.attempts[-1]['provider_metadata']['response_model'] is None
    assert result.workspace_v1['delivery']['status'] in ('no_changes','exported')



def test_retry_config_positional_interface_is_preserved():
    assert RetryConfig(0).max_retries==0 and not RetryConfig(0).stop_on_unknown_usage


@pytest.mark.asyncio
async def test_observed_model_missing_never_falls_back_to_requested_alias():
    client=LLMClient('https://offline.invalid/v1','','requested-alias',audit_metadata=True)
    data=body();data.pop('model');data.pop('system_fingerprint')
    result=client._parse_response(data)
    assert result.provider_metadata['response_model'] is None
    assert result.provider_metadata['system_fingerprint'] is None
    await client.aclose()


@pytest.mark.asyncio
async def test_partial_stream_error_keeps_observed_metadata_and_stops_next_request(tmp_path):
    h=runpy.run_path(str(Path(__file__).with_name('test_workspace_completion.py')))
    loop,session,model,profile,executor=h['setup'](tmp_path,[])
    loop._retry_config.stop_on_unknown_usage=True
    loop.use_llm_stream=True
    calls=[]
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"id":"partial-1","model":"served-partial","choices":[{"delta":{"content":"partial"}}]}\n\n'.replace(b'\\n',b'\n')
            raise httpx.ReadTimeout('synthetic-sensitive')
    async def handler(request):
        calls.append(True)
        return httpx.Response(200,stream=BrokenStream(),headers={'x-request-id':'partial-request'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        loop.llm=LLMClient('https://offline.invalid/v1','','requested',audit_metadata=True,http_client=http)
        with pytest.raises(PaidCallStopped): await loop._call_llm([{'role':'user','content':'x'}],[],0)
        with pytest.raises(PaidCallStopped): await loop._call_llm([{'role':'user','content':'x'}],[],1)
    evidence=loop.attempt_sink.attempts[-1].provider_metadata
    assert evidence['response_model']=='served-partial' and evidence['provider_request_id']=='partial-request'
    assert evidence['response_id']=='partial-1' and evidence['raw_usage'] is None
    assert 'synthetic-sensitive' not in json.dumps(evidence) and calls==[True]
    session.close()


@pytest.mark.asyncio
async def test_real_nonstream_tool_roundtrip_persists_model_evidence_in_session(tmp_path):
    h=runpy.run_path(str(Path(__file__).with_name('test_workspace_completion.py')))
    loop,session,model,profile,executor=h['setup'](tmp_path,[])
    loop._retry_config.stop_on_unknown_usage=True
    calls=[]
    async def handler(request):
        payload=json.loads(request.content);calls.append(payload)
        data=body(finish='tool_calls' if len(calls)==1 else 'stop')
        data['usage']=GOOD|{'provider_extra':{'charged':14}}
        if len(calls)==1:
            data['choices'][0]['message']={'content':None,'tool_calls':[{'id':'write','type':'function',
                'function':{'name':'write_file','arguments':json.dumps({'path':'pkg/calc.py','content':h['GOOD'].decode()})}}]}
        else:
            assert any(m.get('role')=='tool' for m in payload['messages'])
        return httpx.Response(200,json=data,headers={'x-request-id':'request-'+str(len(calls))})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        loop.llm=LLMClient('https://offline.invalid/v1','','requested-alias',audit_metadata=True,http_client=http)
        with session: result=await session.run(loop,'public fixture repair')
    replay=RunSession.replay(session.store.directory)
    assert len(calls)==2 and profile.transport.commands==1 and result.workspace_v1['development_validation']['status']=='passed'
    assert [a['provider_metadata']['provider_request_id'] for a in replay.attempts]==['request-1','request-2']
    assert replay.attempts[0]['provider_metadata']['raw_usage']['provider_extra']=={'charged':14}
    assert result.llm_attempts[0]['provider_metadata']['response_model']=='actual-served-model'


@pytest.mark.asyncio
async def test_large_usage_audit_is_explicitly_unknown_instead_of_silent_loss():
    client=LLMClient('https://offline.invalid/v1','','requested',audit_metadata=True)
    response=client._parse_response(body(usage=GOOD|{'oversize':'x'*70000}))
    policy=RetryPolicy(RetryConfig(stop_on_unknown_usage=True),model_ledger=ledger())
    async def fn(*args): return response
    with pytest.raises(PaidCallStopped): await policy.execute(fn,[],[],max_output_tokens=64)
    metadata=policy.sink.attempts[-1].provider_metadata
    assert metadata['raw_usage_omitted']=='size_limit' and metadata['raw_usage'] is None
    assert len(metadata['raw_usage_sha256'])==64
    await client.aclose()
