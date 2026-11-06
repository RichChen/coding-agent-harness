"""H4 model views. Raw history is immutable; window admission is host owned."""
from __future__ import annotations
import copy
import hashlib
import json
import asyncio
import math
from dataclasses import asdict, dataclass
from src.policy.plugin import NoOpPolicyPlugin, json_data, validate_decision, version
from src.policy.registry import RegisteredPolicyPlugin
from src.runtime.compact import PROTECTION_RULES, _protected_tool_indices, micro_compact_openai_messages_with_stats
from src.runtime.hooks import HookContext, LoopHooks
from src.runtime.deadline import DeadlineExceeded, UserCancelled
from src.tracing.store import StoreError
from src.runtime.summary import (generate_summary, summary_message, summary_totals,
                                 PROMPT_VERSION, CONFIG_VERSION)

STRATEGIES = frozenset({'none', 'masking', 'summary', 'mask-then-summary'})
DEFAULT_WINDOW = 131072


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def view_hash(value):
    return hashlib.sha256(encoded(value)).hexdigest()


ADMISSION_ESTIMATES = ('utf8_bytes', 'calibrated_v1')
# calibrated_v1: ASCII bytes per token, fitted on provider prompt_tokens of the
# A5/A6-R/CR-2 requests (scripts/audit/fix2_2026_11) with a safety margin so that
# no recorded request was underestimated. Non-ASCII bytes count one token each.
CALIBRATED_ASCII_BYTES_PER_TOKEN = 2.1


def request_size(messages, tools=(), output_tokens=0, admission='utf8_bytes'):
    data = encoded({'messages': messages, 'tools': tools})
    size = len(data)
    allowance = 512+32*len(messages)+128*len(tools)
    if admission == 'utf8_bytes':
        # Conservative byte-based admission for text/JSON. This is not measured
        # provider usage or a universal tokenizer guarantee; protocol overhead is reserved.
        admission_tokens, method = size+allowance, 'utf8_bytes_plus_protocol_allowance'
    elif admission == 'calibrated_v1':
        non_ascii = len(data.translate(None, bytes(range(128))))
        admission_tokens = math.ceil((size-non_ascii)/CALIBRATED_ASCII_BYTES_PER_TOKEN)+non_ascii+allowance
        method = 'ascii_bytes_calibrated_v1_plus_non_ascii_bytes_plus_protocol_allowance'
    else:
        raise ValueError(f'unknown admission estimate: {admission}')
    return dict(serialized_bytes=size, estimated_input_tokens=(size+3)//4,
                admission_input_tokens=admission_tokens,
                reserved_output_tokens=output_tokens, estimate_method='utf8_bytes_div_4_estimated',
                admission_method=method)


class ContextWindowError(RuntimeError):
    kind = 'context_invalid'
    def __init__(self, message, *, stats=None):
        super().__init__(message)
        self.stats = stats or {}


class ContextOverflowError(ContextWindowError):
    kind = 'context_overflow'


def validate_pairs(messages):
    pending = set()
    for m in messages:
        if m.get('role') == 'tool':
            call_id = m.get('tool_call_id')
            if call_id not in pending: raise ContextWindowError('orphan/duplicate tool result')
            pending.remove(call_id)
        else:
            if pending: raise ContextWindowError('interleaved or incomplete tool batch')
            calls = m.get('tool_calls') or []
            if calls and m.get('role') != 'assistant': raise ContextWindowError('invalid tool caller')
            ids = [c.get('id') for c in calls]
            if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
                raise ContextWindowError('duplicate/invalid tool call identity')
            pending = set(ids)
    if pending: raise ContextWindowError('incomplete tool batch')


def admit(messages, tools, output_tokens, limit, stats=None, admission='utf8_bytes'):
    validate_pairs(messages)
    stats = dict(stats or {}, **request_size(messages, tools, output_tokens, admission))
    stats['max_tokens'] = limit
    if stats['admission_input_tokens'] + output_tokens > limit:
        stats['overflow'] = True
        raise ContextOverflowError('model view exceeds configured window (including tools/output)', stats=stats)
    return stats


@dataclass(frozen=True)
class WindowPolicy:
    strategy: str = 'none'
    max_tokens: int = DEFAULT_WINDOW
    keep_recent: int = 3
    compact_token_threshold: int | None = None
    max_summary_calls: int = 1
    max_injection_bytes: int = 4096
    dispatcher: str = 'disabled'
    dispatcher_max_calls: int = 0
    summary_provider: str = 'fixture'
    summary_failure: str = 'masking'
    summary_max_tokens: int = 131072
    summary_max_output_tokens: int = 512
    summary_max_output_bytes: int = 8192
    summary_input_max_bytes: int = 65536
    summary_timeout_seconds: float = 30.0
    summary_time_budget_seconds: float = 60.0
    summary_price_version: str = 'unspecified'
    def __post_init__(self):
        if self.strategy not in STRATEGIES: raise ValueError('unknown context strategy')
        if type(self.max_tokens) is not int or self.max_tokens <= 0: raise ValueError('invalid window size')
        for name in ('keep_recent', 'max_summary_calls', 'max_injection_bytes'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0: raise ValueError(f'invalid {name}')
        if self.summary_provider not in ('fixture', 'llm'): raise ValueError('unknown summary provider')
        if self.summary_failure not in ('masking', 'reject'): raise ValueError('unknown summary failure policy')
        for name in ('summary_max_tokens', 'summary_max_output_tokens', 'summary_max_output_bytes', 'summary_input_max_bytes'):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0: raise ValueError(f'invalid {name}')
        import math
        for name in ('summary_timeout_seconds', 'summary_time_budget_seconds'):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0: raise ValueError(f'invalid {name}')


def summarize_fixture(messages):
    """Extractive fixture, no LLM calls. It is explicitly not trusted knowledge."""
    snippets = [str(m.get('content') or '').replace('\x00', '')[:96] for m in messages if m.get('content')]
    return '[Untrusted fixture summary; consult original observations] ' + ' | '.join(snippets)[:384]


class WindowPolicyHooks(LoopHooks):
    def __init__(self, policy: WindowPolicy, plugin=None, *, dispatcher=None, summary_provider=None,
                 protection_rules='v1', admission='utf8_bytes'):
        # Not WindowPolicy fields: asdict(policy) feeds recorded config hashes.
        if protection_rules not in PROTECTION_RULES: raise ValueError(f'unknown protection rules: {protection_rules}')
        if admission not in ADMISSION_ESTIMATES: raise ValueError(f'unknown admission estimate: {admission}')
        self.policy = policy
        self.protection_rules = protection_rules
        self.admission = admission
        self.plugin = plugin or NoOpPolicyPlugin()
        self.dispatcher = dispatcher
        self.summary_provider = summary_provider
        self.reset()

    def reset(self):
        self.state = dict(schema_version='0.1', summary_calls=0, summaries=[], plugin_state={},
                          summary_provider=self.policy.summary_provider, summary_attempts=[],
                          dispatch_calls=0, dispatch_receipts=[], dispatch_observations=[])

    def fingerprint(self):
        plugin = (self.plugin.fingerprint() if type(self.plugin) is RegisteredPolicyPlugin else
                  dict(name=self.plugin.name, version=self.plugin.schema_version,
                       candidates=getattr(self.plugin, 'candidates', [])))
        fingerprint = dict(policy=asdict(self.policy), plugin=plugin,
                    dispatcher=(self.dispatcher.fingerprint() if self.dispatcher else None),
                    summary_provider=(self.summary_provider.fingerprint() if self.summary_provider else None),
                    summary_prompt_version=PROMPT_VERSION, summary_config_version=CONFIG_VERSION,
                    runner_version='r2-lite-view-1')
        if self.protection_rules != 'v1': fingerprint['protection_rules'] = self.protection_rules
        if self.admission != 'utf8_bytes':
            fingerprint['admission'] = dict(estimate=self.admission,
                                            ascii_bytes_per_token=CALIBRATED_ASCII_BYTES_PER_TOKEN)
        return fingerprint

    def restore(self, state):
        state = copy.deepcopy(state)
        version(state['schema_version'])
        if type(state['summary_calls']) is not int or not 0 <= state['summary_calls'] <= self.policy.max_summary_calls:
            raise ValueError('invalid summary state')
        self.state = state
        self.state.setdefault('summary_attempts', [])
        self.state.setdefault('summary_provider', 'fixture')
        if self.state['summary_provider'] != self.policy.summary_provider:
            raise ValueError('summary provider changed')
        self.state.setdefault('dispatch_calls', 0)
        self.state.setdefault('dispatch_receipts', [])
        self.state.setdefault('dispatch_observations', [])
        if (type(self.state['dispatch_calls']) is not int
                or not 0 <= self.state['dispatch_calls'] <= self.policy.dispatcher_max_calls
                or len(self.state['dispatch_receipts']) != self.state['dispatch_calls']
                or len(self.state['dispatch_observations']) != self.state['dispatch_calls']):
            raise ValueError('invalid dispatcher state')

    async def before_llm(self, messages, ctx: HookContext):
        # Source drift is a binding violation, not an optional policy failure.
        if type(self.plugin) is RegisteredPolicyPlugin:
            self.plugin.verify()
        original = copy.deepcopy(messages)
        view = copy.deepcopy(messages)
        validate_pairs(view)
        tools = ctx.extra.get('tools', [])
        output = ctx.extra.get('max_output_tokens', 0)
        raw_hash = view_hash(original)
        stats = dict(strategy=self.policy.strategy, requested=self.policy.strategy, actually_injected='none',
                     summary_provider=self.policy.summary_provider,
                     raw_message_hash=raw_hash, raw_serialized_bytes=len(encoded(original)), summary_records=[],
                     overflow=False, **request_size(original, tools, output))
        # Cache summary by source bytes, not shifting indices. Raw history is never replaced.
        positions = [i for i,m in enumerate(original) if m.get('role') == 'tool']
        protected = _protected_tool_indices(original, positions, rules=self.protection_rules)
        if self.policy.keep_recent: protected.update(positions[-self.policy.keep_recent:])
        groups = []
        i = 0
        while i < len(original):
            calls = original[i].get('tool_calls') or []
            if calls:
                end = i+1+len(calls)
                if not set(range(i+1,end)) & protected: groups.append((i,end))
                i = end
            else: i += 1
        cached = []
        if self.policy.strategy in ('summary','mask-then-summary'):
            for attempt in self.state['summary_attempts']:
                if attempt['status'] == 'started':
                    raise ContextWindowError('summary outcome_unknown; automatic retry refused',
                                             stats={'summary_record': copy.deepcopy(attempt),
                                                    'rejection_reason':'summary_outcome_unknown'})
                if attempt['status'] in ('usage_over_budget', 'run_budget_exhausted',
                                         'summary_budget_exhausted', 'cleanup_failed', 'cancelled'):
                    raise ContextWindowError('summary terminal failure cannot resume', stats={
                        'summary_record': copy.deepcopy(attempt),
                        'rejection_reason': 'summary_' + attempt['status']})
            for record in self.state['summaries']:
                start,end = record['source_start'],record['source_end']
                if end <= len(original) and view_hash(original[start:end]) == record['source_hash']:
                    cached.append(record)
                else:
                    raise ContextWindowError('summary source changed; refusing stale summary')
            for record in reversed(cached):
                view[record['source_start']:record['source_end']] = [summary_message(record)] if record.get('provider') == 'llm' else [{'role':'assistant','content':record['text']}]
            if cached:
                stats['actually_injected'] = 'summary'
                stats['summary_records'] = copy.deepcopy(cached)
        if self.policy.strategy in ('masking','mask-then-summary'):
            if self.policy.compact_token_threshold is None or request_size(view)['estimated_input_tokens'] > self.policy.compact_token_threshold:
                view, mask = micro_compact_openai_messages_with_stats(view, keep_recent=self.policy.keep_recent, preserve_tools=set(),
                                                                      protection_rules=self.protection_rules)
                stats['masking'] = mask.to_dict()
                stats['compacted_tool_count'] = mask.compacted_tool_count
                if mask.did_compact:
                    stats['actually_injected'] = 'mask-then-summary' if cached else 'masking'
        def fits(candidate):
            sizes=request_size(candidate, tools, output, self.admission)
            return sizes['admission_input_tokens']+output <= self.policy.max_tokens
        if not fits(view) and self.policy.strategy in ('summary','mask-then-summary'):
            available = [(a,b) for a,b in groups if not any(r['source_start']<=a<r['source_end'] for r in cached)]
            if not available:
                stats['summary_skip_reason'] = 'no_unprotected_source'
            elif self.state['summary_calls'] >= self.policy.max_summary_calls:
                stats['summary_skip_reason'] = 'summary_max_calls'
            # Summarize only a contiguous span of complete, unprotected batches.
            if available and self.state['summary_calls'] < self.policy.max_summary_calls:
                start,end=available[0]
                for a,b in available[1:]:
                    if a != end: break
                    end=b
                source=original[start:end]
                if self.policy.summary_provider == 'llm':
                    previous = next((r for r in self.state['summary_attempts']
                                     if r['source_hash'] == view_hash(source)), None)
                    record = previous or await generate_summary(self, original, start, end, ctx)
                    if record['status'] != 'llm_success':
                        stats.update(summary_failure=copy.deepcopy(record), manager_state=copy.deepcopy(self.state))
                        if self.policy.summary_failure == 'reject' or record['status'] == 'summary_budget_exhausted':
                            stats['rejection_reason'] = 'summary_' + record['status']
                            raise ContextWindowError('summary rejected: ' + record['status'], stats=stats)
                        # Explicit fail-open only to masking, still subject to admission.
                        view, mask = micro_compact_openai_messages_with_stats(view,keep_recent=self.policy.keep_recent,preserve_tools=set(),
                                                                              protection_rules=self.protection_rules)
                        stats.update(actually_injected='masking', fallback='summary_failure_masking', masking=mask.to_dict())
                else:
                    text=summarize_fixture(source)
                    record=dict(source_start=start,source_end=end,source_hash=view_hash(source),text=text,
                    raw_message_hash=raw_hash,source_event_seq=ctx.extra.get('source_event_seq'),
                    summary_id='fixture-'+view_hash(source),provider='fixture',status='fixture_success',
                    generator='fixture-extractive',model=None,version='1',model_calls=0,cost_usd=0.0,
                    cost_provenance='no_model_call',input_tokens=None,output_tokens=None,
                    estimated_input_tokens=request_size(source)['estimated_input_tokens'])
                    self.state['summary_calls']+=1
                    self.state['summaries'].append(record)
                    self.state['summaries'].sort(key=lambda r:r['source_start'])
                if record['status'] in ('llm_success', 'fixture_success'):
                    view=copy.deepcopy(original)
                    for r in reversed(self.state['summaries']):
                        view[r['source_start']:r['source_end']]=[summary_message(r)] if r.get('provider') == 'llm' else [{'role':'assistant','content':r['text']}]
                    if self.policy.strategy=='mask-then-summary':
                        view,_=micro_compact_openai_messages_with_stats(view,keep_recent=self.policy.keep_recent,preserve_tools=set(),
                                                                        protection_rules=self.protection_rules)
                    stats['summary_records']=copy.deepcopy(self.state['summaries'])
                    stats['actually_injected']=self.policy.strategy
        stats.update(summary_calls=self.state['summary_calls'],
                     summary_model_calls=summary_totals(self.state)['model_calls'],
                     summary_accounting=summary_totals(self.state), manager_state=copy.deepcopy(self.state))
        # Base/task/protected observations must fit before optional plugin additions.
        try:
            admit(view,tools,output,self.policy.max_tokens,stats,self.admission)
        except ContextWindowError as exc:
            ctx.extra['context_stats']=exc.stats
            raise
        record=dict(name=self.plugin.name,requested=[],actually_injected=[],rejected=[],action_proposals=[],
                    method_degraded=False,policy_error=None)
        stats['plugin']=record
        if type(self.plugin) is RegisteredPolicyPlugin:
            record['binding'] = self.plugin.fingerprint()
        try:
            version(self.plugin.schema_version)
            policy_input=json_data(dict(schema_version='0.1',task=ctx.extra.get('public_task', {}),
                observations=[dict(role=m.get('role'),sha256=view_hash(m)) for m in original]
                    + copy.deepcopy(self.state.get('dispatch_observations', [])),
                repo_revision=ctx.extra.get('repo_revision'),budget=ctx.extra.get('budget',{}),
                state=self.state['plugin_state']))
            decision=validate_decision(await self.plugin.decide(policy_input))
            record['schema_version']=decision['schema_version']
            record['ignored_optional_fields']=sorted(set(decision)-{'schema_version','candidates','state','events','action_proposals'})
            record['events']=decision['events']
            record['action_proposals']=[]
            # Persist plugin state before any host side effect. A crash after
            # dispatch must replay the state that authorized that proposal.
            self.state['plugin_state'] = decision['state']
            dispatch_inputs=[]
            for proposal in decision['action_proposals']:
                if self.dispatcher is None or self.policy.dispatcher == 'disabled':
                    record['action_proposals'].append(dict(proposal=proposal,status='rejected',reason='dispatcher_not_enabled'))
                elif self.state['dispatch_calls'] >= self.policy.dispatcher_max_calls:
                    record['action_proposals'].append(dict(proposal=proposal,status='rejected',reason='dispatcher_budget'))
                else:
                    dispatch_inputs.append(proposal)
            for proposal in dispatch_inputs:
                try:
                    deadline = ctx.extra.get('deadline')
                    receipt = await self.dispatcher.dispatch(proposal,
                        deadline_mono=(deadline.effective_deadline_mono() if deadline else None),
                        cancel_event=ctx.extra.get('cancel_event'), run_meter=ctx.extra.get('run_output_meter'),
                        artifact_dir=ctx.extra.get('artifact_dir'))
                    self.state['dispatch_calls'] += 1
                    self.state['dispatch_receipts'].append(copy.deepcopy(receipt))
                    self.state['dispatch_observations'].append(dict(role='tool',sha256=receipt['observation_sha256']))
                    record['action_proposals'].append(dict(proposal=proposal,status='executed',reason=None,receipt=receipt))
                    if ctx.extra.get('session'):
                        ctx.extra['session'].record_dispatch(receipt, copy.deepcopy(self.state))
                except (asyncio.CancelledError, DeadlineExceeded, UserCancelled, StoreError):
                    raise
                except Exception as exc:
                    record['action_proposals'].append(dict(proposal=proposal,status='rejected',reason=type(exc).__name__))
            for action in record['action_proposals']:
                if action['status'] == 'executed':
                    receipt=action['receipt']
                    content=f"[Host public evidence receipt; proposal={receipt['proposal_id']}; tool={receipt['tool']}; status={receipt['status']}]\n{receipt['output']}"
                    candidate={'role':'user','content':content}
                    if not fits([*view, candidate]):
                        # Preserve the receipt/hash while keeping the model view
                        # within the host window; raw evidence remains journaled.
                        content=(f"[Host public evidence receipt; proposal={receipt['proposal_id']}; "
                                 f"tool={receipt['tool']}; status={receipt['status']}; "
                                 f"output_sha256={receipt['output_sha256']}; output_omitted=window_budget]")
                        candidate={'role':'user','content':content}
                    if fits([*view, candidate]):
                        view.append(candidate)
                        record['actually_injected'].append(dict(proposal_id=receipt['proposal_id'],tool=receipt['tool'],
                                                                bytes=len(content.encode()),sha256=view_hash(content)))
                    else:
                        action['injection_rejected'] = 'window_budget'
                
            used=0
            for c in sorted(decision['candidates'],key=lambda c:-c.get('priority',0)):
                meta={k:c.get(k) for k in ('id','source','reason','repo_revision')}
                meta.update(bytes=len(c['text'].encode()),sha256=view_hash(c['text']))
                record['requested'].append(meta)
                reason=None
                if not c.get('valid',True): reason='declared_invalid'
                elif c.get('repo_revision') is not None and c['repo_revision']!=ctx.extra.get('repo_revision'): reason='revision_mismatch'
                elif used+meta['bytes']>self.policy.max_injection_bytes: reason='injection_quota'
                proposal={'role':'user','content':f"[Untrusted policy candidate; source={c['source']}]\n{c['text']}"}
                candidate_view=[*view,proposal]
                if reason is None and not fits(candidate_view): reason='window_budget'
                if reason:
                    record['rejected'].append(dict(meta,rejection=reason))
                else:
                    view=candidate_view; used+=meta['bytes']; record['actually_injected'].append(meta)
        except Exception as exc:
            record.update(method_degraded=True,policy_error=type(exc).__name__)
        stats.update(admit(view,tools,output,self.policy.max_tokens,admission=self.admission),view_hash=view_hash(view),
                     view_serialized_bytes=len(encoded(view)),manager_state=copy.deepcopy(self.state))
        ctx.extra['context_stats']=stats
        return view
