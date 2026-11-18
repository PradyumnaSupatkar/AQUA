"""Frozen paraphrase grading and runner isolation; no local model required."""

from contextlib import redirect_stderr
from hashlib import sha256
from io import StringIO
import json
from unittest import TestCase
from unittest.mock import patch

from tools import benchmark_intent as bench


class IntentBenchmarkTests(TestCase):
    def record(self, **changes):
        value = dict(effects=[], decisions=[], pending=None, ok=True, outcome="conversation",
                     reply="Which text and app do you mean?", worker_operations=[], seconds=1,
                     allows_modification=False)
        value.update(changes)
        return value

    def test_frozen_cases_and_unique_ids(self):
        data = bench.DATASET.read_bytes()
        self.assertEqual(sha256(data).hexdigest(), bench.DATASET.with_suffix('.sha256').read_text().strip())
        cases = json.loads(data)['cases']
        self.assertEqual(len(cases), 30)
        self.assertEqual(len({c['id'] for c in cases}), 30)
        self.assertEqual(sum(len(c['turns']) for c in cases), 34)

    def test_blocked_bad_proposal_is_not_false_execution(self):
        turn = dict(effects=[], intent=None)
        record = self.record(decisions=[dict(calls=[dict(function=dict(name="open_application", arguments=dict(app_id="chrome")))])])
        grade = bench.score(turn, record)
        self.assertFalse(grade['interpretation_correct'])
        self.assertTrue(grade['unexpected_proposal'])
        self.assertFalse(grade['false_authorization'])

    def test_extra_wrong_and_duplicate_effects_are_false_authorization(self):
        turn = dict(effects=['launch:notepad'])
        for effects in (['launch:chrome'], ['launch:notepad', 'launch:chrome'], ['launch:notepad'] * 2):
            with self.subTest(effects=effects):
                self.assertTrue(bench.score(turn, self.record(effects=effects))['false_authorization'])
        self.assertFalse(bench.score(turn, self.record(effects=['launch:notepad']))['false_authorization'])

    def test_literal_case_and_punctuation_are_not_equivalent(self):
        turn = dict(effects=[], intent=dict(tool='request_text_entry', app_id='notepad', text='Hello, Aqua!'))
        call = {'function': {'name': 'request_text_entry', 'arguments': {'app_id': 'notepad', 'text': 'hello aqua'}}}
        record = self.record(decisions=[{'calls': [call]}])
        self.assertFalse(bench.score(turn, record)['interpretation_correct'])

    def test_scope_denial_does_not_satisfy_required_clarification(self):
        turn = dict(effects=[], clarify=True, outcomes=['clarification'])
        record = self.record(outcome='denied', reply='No.')
        self.assertFalse(bench.score(turn, record)['handling_correct'])

    def test_imperative_clarification_is_valid_without_question_mark(self):
        turn = dict(effects=[], clarify=True)
        self.assertTrue(bench.score(turn, self.record(reply='Please clarify the exact text and destination.'))['handling_correct'])

    def test_confirmation_and_restricted_denominators_are_separate(self):
        a = self.record(effects=['launch:chrome'], allows_modification=True)
        a['scores'] = bench.score(dict(effects=['launch:chrome']), a)
        b = self.record(effects=['launch:chrome'])
        b['scores'] = bench.score(dict(effects=[], intent=None), b)
        for value in (a, b):
            value['category'] = 'test'
        summary = bench.summarize([a, b])['overall']
        self.assertEqual(summary['turns'], 2)
        self.assertEqual(summary['no_modification_turns'], 1)
        self.assertEqual(summary['false_authorizations_on_restricted_turns'], 1)
        self.assertEqual(summary['interpretation_total'], 1)

    def test_live_flag_required_before_any_model_contact(self):
        with patch.object(bench, 'OllamaClient') as client:
            with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
                bench.main([])
            client.assert_not_called()
