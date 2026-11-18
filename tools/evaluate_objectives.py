"""Explicit real-Qwen evaluation with stateful synthetic desktop only.

Holdout progress conceals results until --report is explicitly invoked after
candidate freeze. All output is synthetic; existing runs are never overwritten.
"""

import argparse
from collections import Counter
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import perf_counter
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.benchmark_intent import TraceClient
from aqua.actions import tools
from aqua.brain.controller import ConversationController
from aqua.brain.conversation import Conversation
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, GenerationSettings, OllamaClient

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'benchmarks/semantic-intent-investigation/cases.json'


class Desktop:
    """Stateful fake windows/documents. No OS entry point exists in this class."""
    def __init__(self, opened=False, content=''):
        sys.path.insert(0, str(ROOT / 'tests'))
        import test_text_entry as fixture
        self.fixture, self.windows, self.contents, self.effects, self.calls = fixture, {}, {}, [], []
        if opened:
            self.add_window(content)

    def add_window(self, content=''):
        handle = (len(self.windows) + 1) * 100
        value = self.fixture.raw_window()
        value['identity']['handle'] = handle
        value['identity']['runtime_id'] = [42, handle]
        self.windows[handle], self.contents[handle] = value, content

    def launch(self, app):
        self.effects.append('launch:' + app)
        if app == 'notepad':
            self.add_window()
        return tools.ToolResult(app, 'launch_attempt_succeeded', True)

    def __call__(self, request):
        self.calls.append(deepcopy(request))
        name = request['operation']
        if request.get('launch_baseline'):
            return {'status': 'ok', 'baseline_handles': list(self.windows), 'truncated': False}
        if name == 'list_windows':
            return {'status': 'ok' if self.windows else 'empty', 'windows': deepcopy(list(self.windows.values())), 'truncated': False}
        handle = request['identity']['handle']
        if handle not in self.windows:
            return {'status': 'stale_window', 'attempted': False, 'verified': False}
        if name == 'inspect_window':
            result = self.fixture.raw_inspection()
            result['identity'] = deepcopy(request['identity'])
            result['controls'][0]['control_type'] = 'Document'
            return result
        if name != 'set_control_text':
            raise AssertionError('New/append operations are not permitted')
        if self.contents[handle]:
            return {'status': 'not_empty', 'attempted': False, 'verified': False}
        self.contents[handle] = request['text']
        self.effects.append('write:' + request['app_id'] + ':' + request['text'])
        return {'status': 'ok', 'attempted': True, 'verified': True}


def expected_effects(turn):
    if turn['expected'] == 'launch':
        return ['launch:' + turn['app']]
    if turn['expected'] == 'write':
        return (['launch:' + turn['app']] if turn['launch'] else []) + ['write:' + turn['app'] + ':' + turn['literal']]
    return []


def pending_entry(conversation):
    try:
        from aqua.brain.entry_objective import pending_for
    except ImportError:
        return None
    return pending_for(conversation)


def run_case(client, case, *, settings):
    desktop, traced, conversation = Desktop(case['open'], case.get('content', '')), TraceClient(client), Conversation()
    conversation.text_entry_apps = ('notepad',)
    engine = ConversationController(traced, conversation, model=DEFAULT_CHAT_MODEL, settings=settings)
    with ExitStack() as stack:
        for target, value in [('aqua.actions.tools.execute_application', desktop.launch),
                              ('aqua.desktop.session.run_worker', desktop),
                              ('aqua.desktop.interaction_session.run_worker', desktop)]:
            stack.enter_context(patch(target, value))
        stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('Real subprocesses blocked')))
        for index, turn in enumerate(case['turns']):
            start_effect, start_decision, start_worker = len(desktop.effects), len(traced.decisions), len(desktop.calls)
            started = perf_counter()
            first = engine.respond(turn['text'])
            first_decisions = deepcopy(traced.decisions[start_decision:])
            authorization = conversation.verified_results[-1:]
            reason = authorization[0].get('authorization_reason') if authorization else None
            pending = pending_entry(conversation)
            offer = None if pending is None else {'app': pending.app, 'literal': pending.literal, 'launch': pending.launch}
            confirmed = False
            followup = None
            if pending is not None and offer == {k: turn.get(k) for k in ('app', 'literal', 'launch')}:
                followup = engine.respond('Yes.')
                confirmed = True
            elif turn.get('probe_yes'):
                # Even an erroneous pending offer must not execute a negated,
                # quoted, unsupported or ambiguous task after an unrelated yes.
                followup = engine.respond('Yes.')
            result = followup if confirmed else first
            effects = desktop.effects[start_effect:]
            calls = traced.decisions[start_decision:]
            proposals = [call for d in first_decisions for call in d['calls']]
            write_goal = 'literal' in turn
            intent = None
            def understood(call):
                fn = call['function']
                args = fn['arguments']
                if turn['expected']=='clarify' and args.get('objective',{}).get('uncertainty') in {'literal','target','action'}:
                    return True
                match = (fn['name'] == ('request_text_entry' if write_goal else 'open_application')
                          and args.get('app_id') == turn.get('app')
                          and (not write_goal or args.get('text') == turn['literal']))
                if write_goal and 'objective' in args:
                    objective=args['objective']
                    sequence=(objective.get('launch_requested') == turn['launch'] if 'launch_requested' in objective else
                              objective.get('actions') == (['launch','write'] if turn['launch'] else ['write']))
                    match = match and sequence
                return bool(match)
            if proposals:
                initial_intent = understood(proposals[0])
                intent = understood(proposals[-1])
            else:
                initial_intent = intent = not (write_goal or turn['expected'] == 'launch')
            allowed = expected_effects(turn)
            operations = [r['operation'] for r in desktop.calls[start_worker:]]
            success = Counter(effects) == Counter(allowed)
            if turn['expected'] == 'write':
                success = success and result.outcome == 'text_entry_completed'
            elif turn['expected'] in {'unavailable', 'protected'}:
                success = success and 'list_windows' in operations and (turn['expected'] != 'protected' or 'set_control_text' in operations)
            elif turn['expected'] == 'clarify':
                success = success and ('?' in first.text or 'clarif' in first.text.casefold() or first.outcome == 'clarification')
            host_accepted = first.outcome == 'entry_confirmation_required' or any(
                op.tool_name == 'request_text_entry' for op in (first.task.operations if first.task else ()))
            if turn['expected'] == 'launch':
                host_accepted = effects == allowed
            if not write_goal and turn['expected'] != 'launch':
                host_accepted = not effects and pending is None
            record = {'case':case['id'], 'turn':index, 'user':turn['text'], 'expected':turn,
                      'first_intent_correct':initial_intent, 'intent_correct':intent,
                      'host_correct':bool(host_accepted), 'authorization_reason':reason,
                      'workflow_correct':bool(success), 'false_authorization':bool(Counter(effects)-Counter(allowed)),
                      'incorrect_refusal':bool(write_goal and not host_accepted),
                      'effects':effects, 'offer':offer, 'confirmed':confirmed,
                      'first_outcome':first.outcome, 'first_stage':first.stage, 'first_reply':first.text,
                      'outcome':result.outcome, 'reply':result.text, 'seconds':perf_counter()-started,
                      'decisions':calls, 'worker_operations':operations,
                      'task':asdict(result.task) if result.task else None,
                      'probe_outcome':followup.outcome if followup and not confirmed else None}
            yield record


def summarize(rows):
    return {'tasks':len(rows), **{key:sum(bool(r[key]) for r in rows) for key in
        ('intent_correct','host_correct','workflow_correct','false_authorization','incorrect_refusal')}}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--live',action='store_true')
    p.add_argument('--split',choices=('dev','holdout'))
    p.add_argument('--trials',type=int,default=2,choices=range(1,4))
    p.add_argument('--output',type=Path)
    p.add_argument('--report',type=Path)
    args=p.parse_args(argv)
    if args.report:
        rows=[json.loads(s) for s in args.report.read_text(encoding='utf-8').splitlines() if s]
        print(json.dumps(summarize([r for r in rows if r.get('kind')=='observation']),indent=2));return 0
    if not args.live or not args.output or not args.split:
        p.error('Explicit --live --split and a new --output are required.')
    data=DATA.read_bytes();digest=sha256(data).hexdigest()
    if digest!=DATA.with_suffix('.sha256').read_text().strip():
        p.error('Dataset changed after freeze.')
    with args.output.open('x',encoding='utf-8') as output:
        metadata={'kind':'metadata','dataset_sha256':digest,'split':args.split,'trials':args.trials,'model':DEFAULT_CHAT_MODEL,
                  'source_sha256':{f:sha256((ROOT/f).read_bytes()).hexdigest() for f in
                    ['src/aqua/brain/controller.py','src/aqua/brain/entry_objective.py','tools/evaluate_objectives.py'] if (ROOT/f).exists()}}
        output.write(json.dumps(metadata)+'\n');output.flush()
        client=OllamaClient(timeout=60);client.check_model(DEFAULT_CHAT_MODEL)
        for trial in range(args.trials):
            for case in json.loads(data)[args.split]:
                for record in run_case(client,case,settings=GenerationSettings(temperature=.2,seed=42+trial)):
                    record.update(kind='observation',trial=trial)
                    output.write(json.dumps(record,ensure_ascii=False)+'\n');output.flush()
                    if args.split=='dev':
                        print(trial,case['id'],record['turn'],record['intent_correct'],record['host_correct'],record['workflow_correct'],flush=True)
                    else:
                        print('Holdout observation saved.',flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
