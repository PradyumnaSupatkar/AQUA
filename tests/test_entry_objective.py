"""Grounded objectives and semantic consent; all executors are synthetic."""

from unittest import TestCase
from unittest.mock import Mock, patch
from copy import deepcopy
from aqua.brain import entry_objective as objective
from aqua.brain.controller import ConversationController
from aqua.brain.conversation import Conversation
from aqua.brain.ollama import DEFAULT_CHAT_MODEL, GenerationSettings
from aqua.desktop.interaction_session import authorize_text_entry, authorize_launch_text_entry, _entry_literal, _launch_entry_literal
from tools.evaluate_objectives import Desktop
import test_text_entry as fixture
import test_handsfree as speech


def proposal(text='Hello', *, verbs=('Put',), reference='', launch=False):
    return {'app_id':'notepad','text':text,'objective':{
        'launch_requested':launch,
        'app_evidence':'Notepad','text_evidence':text,'source_ref':'ut_testsource',
        'reference_evidence':reference,'uncertainty':'none'}}


class Model:
    def __init__(self,args): self.args,self.calls=deepcopy(args),[]
    def __call__(self,messages,**options):
        self.calls.append((deepcopy(messages),deepcopy(options)))
        definitions={d['function']['name']:d['function'] for d in options['tools']}
        if 'request_text_entry' in definitions:
            args=deepcopy(self.args)
            args['objective']['source_ref']=definitions['request_text_entry']['parameters']['properties']['objective']['properties']['source_ref']['enum'][0]
            return iter(fixture.event('request_text_entry',args))
        for name,key in [('open_application','app_id'),('list_windows',None),('inspect_window','window_id'),('set_control_text','control_id')]:
            if name in definitions:
                arguments={} if key is None else {key:definitions[name]['parameters']['properties'][key]['enum'][0]}
                return iter(fixture.event(name,arguments))
        return iter(speech.native_reply('Finished.'))


class LegacyReproductionTests(TestCase):
    def test_actual_reported_paraphrases_fail_legacy_conditions(self):
        for request, literal in (
            ('Put Hello in Notepad.', 'Hello'),
            ('Put hello in the open window of Notepad.', 'hello'),
            ('open Notepad and put Hello in there.', 'Hello'),
            ('Yes, open Notepad and write hello in there.', 'hello'),
        ):
            with self.subTest(request=request):
                self.assertFalse(authorize_text_entry(request, 'notepad', literal, ('notepad',)))
                self.assertFalse(authorize_launch_text_entry(request, 'notepad', literal, ('notepad',)))
                self.assertIsNone(_entry_literal(request))
                self.assertIsNone(_launch_entry_literal(request, 'notepad'))

    def test_legacy_success_control_has_a_different_fixed_destination_shape(self):
        self.assertTrue(authorize_text_entry('Can you write down Hello into the open Notepad window?', 'notepad', 'Hello', ('notepad',)))
        self.assertFalse(authorize_text_entry('Can you write down Hello in the open window of Notepad?', 'notepad', 'Hello', ('notepad',)))


class GroundingTests(TestCase):
    def assess(self,source,args): return objective.assess(source,args,('notepad',),'ut_testsource')

    def test_paraphrase_is_grounded_without_a_fixed_consent_template(self):
        for source,verbs in [('Put Hello in Notepad.',('Put',)),('Kindly place Hello in Notepad.',('place',)),
                             ('In Notepad, inscribe Hello.',('inscribe',))]:
            with self.subTest(source=source):
                result=self.assess(source,proposal(verbs=verbs))
                self.assertIsNotNone(result.objective)
                self.assertEqual(result.reason,'confirmation_required')

    def test_source_literal_must_match_exactly_and_completely(self):
        for source,args in [('Put Hello in Notepad.',proposal('hello')),
                            ('Put "Hello, world!" in Notepad.',proposal('Hello')),
                            ('Put Hello in Notepad.',proposal('Invented'))]:
            with self.subTest(source=source,args=args):
                self.assertIsNone(self.assess(source,args).objective)

    def test_model_cannot_ground_text_or_app_from_previous_history(self):
        self.assertIsNone(self.assess('Put it there.',proposal('Sam')).objective)
        self.assertIsNone(self.assess('Put Hello in Chrome.',proposal()).objective)

    def test_negated_hypothetical_reported_and_unsupported_requests_fail_closed(self):
        for source in ["Don't Put Hello in Notepad.", 'If I asked you to Put Hello in Notepad, what would happen?',
                       'A message says "Put Hello in Notepad."',
                       'Put Hello in Notepad and Chrome.', 'Put Hello in Notepad and delete files.',
                       'Put Hello in Notepad and email it.', 'Put Hello in Notepad and MysteryPad.']:
            with self.subTest(source=source):
                self.assertIsNone(self.assess(source,proposal()).objective)

    def test_command_words_inside_literal_are_data(self):
        text="Don't launch Chrome."
        result=self.assess(f'Put "{text}" in Notepad.',proposal(text))
        self.assertIsNotNone(result.objective)
        self.assertEqual(result.objective.literal,text)

    def test_uncertainty_never_arms_permission(self):
        args=proposal();args['objective']['uncertainty']='target'
        self.assertIsNone(self.assess('Put Hello in Notepad.',args).objective)

    def test_reference_requires_one_current_app_and_actual_evidence(self):
        args=proposal(verbs=('open','put'),reference='there',launch=True)
        self.assertIsNotNone(self.assess('open Notepad and put Hello in there.',args).objective)
        self.assertIsNone(self.assess('open it and put Hello in there.',args).objective)
        self.assertIsNone(self.assess('open Notepad and put Hello in Chrome.',args).objective)

    def test_model_call_extra_executable_fields_rejected(self):
        call={'function':{'name':'request_text_entry','arguments':{**proposal(),'command':'bad.exe'}}}
        with self.assertRaises(ValueError):objective.validate_call(call)

    def test_source_reference_is_content_addressed_not_an_authorization_token(self):
        self.assertEqual(objective.source_reference('Put Hello in Notepad.'),objective.source_reference('Put Hello in Notepad.'))
        self.assertNotEqual(objective.source_reference('Put Hello in Notepad.'),objective.source_reference('Put hello in Notepad.'))
        args=proposal();args['objective']['source_ref']='ut_old'
        self.assertIsNone(self.assess('Put Hello in Notepad.',args).objective)

    def test_unquoted_substring_cannot_hide_withheld_or_unsupported_consent(self):
        for source,text in [("Don't Put Hello in Notepad.","Don't Put Hello"),
                            ('Put Hello and delete files in Notepad.','Hello and delete files')]:
            with self.subTest(source=source):self.assertIsNone(self.assess(source,proposal(text)).objective)


class WorkflowTests(TestCase):
    def setUp(self):
        self.desktop=Desktop(True)
        for target,value in [('aqua.desktop.session.run_worker',self.desktop),
                              ('aqua.desktop.interaction_session.run_worker',self.desktop),
                              ('aqua.actions.tools.execute_application',self.desktop.launch)]:
            manager=patch(target,value);manager.start();self.addCleanup(manager.stop)
        self.conversation=Conversation();self.conversation.text_entry_apps=('notepad',)

    def engine(self,args,**limits):
        model=Model(args)
        return ConversationController(Mock(stream_chat=Mock(side_effect=model)),self.conversation,
            model=DEFAULT_CHAT_MODEL,settings=GenerationSettings(temperature=.2),**limits)

    def test_exact_reported_paraphrase_only_executes_after_scoped_confirmation(self):
        engine=self.engine(proposal())
        first=engine.respond('Put Hello in Notepad.')
        self.assertEqual(first.outcome,'entry_confirmation_required')
        self.assertEqual(self.desktop.calls,[])
        self.assertEqual(self.desktop.effects,[])
        # Real frontends create a new controller each turn; state is session-local.
        engine2=self.engine(proposal())
        result=engine2.respond('Yes.')
        self.assertEqual(result.outcome,'text_entry_completed')
        self.assertEqual(self.desktop.effects,['write:notepad:Hello'])
        self.assertIsNone(objective.pending_for(self.conversation))
        self.assertEqual(engine2.respond('Yes.').outcome,'stale_confirmation')
        self.assertEqual(self.desktop.effects,['write:notepad:Hello'])

    def test_combined_launch_then_entry_uses_new_window_and_existing_budget(self):
        engine=self.engine(proposal(verbs=('open','put'),reference='there',launch=True))
        first=engine.respond('open Notepad and put Hello in there.')
        self.assertEqual(first.outcome,'entry_confirmation_required')
        result=engine.respond('Yes.')
        self.assertEqual(result.outcome,'text_entry_completed')
        self.assertEqual(self.desktop.contents[100],'')
        self.assertEqual(self.desktop.contents[200],'Hello')
        self.assertEqual(self.desktop.effects,['launch:notepad','write:notepad:Hello'])
        self.assertLessEqual(result.task.decisions,6)
        self.assertEqual(len(result.task.operations),5)

    def test_absent_window_does_not_authorize_an_implicit_launch(self):
        self.desktop.windows.clear();self.desktop.contents.clear()
        engine=self.engine(proposal());engine.respond('Put Hello in Notepad.')
        result=engine.respond('Yes.')
        self.assertEqual(self.desktop.effects,[])
        self.assertIn('list_windows',[r['operation'] for r in self.desktop.calls])
        self.assertNotEqual(result.outcome,'text_entry_completed')

    def test_existing_document_is_not_overwritten(self):
        self.desktop.contents[100]='Existing\r\n'
        engine=self.engine(proposal());engine.respond('Put Hello in Notepad.')
        result=engine.respond('Yes.')
        self.assertEqual(self.desktop.effects,[])
        self.assertEqual(self.desktop.contents[100],'Existing\r\n')
        self.assertNotEqual(result.outcome,'text_entry_completed')

    def test_unrelated_turn_cancellation_clear_expiry_and_revoked_opt_in_invalidate(self):
        for mode in ('unrelated','cancel','clear','expire','revoked','state_clear'):
            with self.subTest(mode=mode):
                self.conversation=Conversation();self.conversation.text_entry_apps=('notepad',)
                engine=self.engine(proposal());engine.respond('Put Hello in Notepad.')
                if mode=='clear':self.conversation.clear()
                elif mode=='cancel':engine.respond('No.')
                elif mode=='unrelated':
                    engine.client.stream_chat.return_value=iter(speech.native_reply('Formula One is motorsport.'))
                    engine.client.stream_chat.side_effect=None
                    engine.respond('What is Formula One?')
                elif mode=='expire':
                    with patch.object(objective,'monotonic',return_value=float('inf')):
                        self.assertIsNone(objective.pending_for(self.conversation))
                elif mode=='revoked':self.conversation.text_entry_apps=()
                else:self.conversation.action_state.clear()
                self.assertEqual(engine.respond('Yes.').outcome,'stale_confirmation')
                self.assertEqual(self.desktop.effects,[])

    def test_confirmation_does_not_reset_model_budget(self):
        engine=self.engine(proposal(),max_model_decisions=3)
        result=engine.respond('Put Hello in Notepad.')
        self.assertIsNone(objective.pending_for(self.conversation))
        self.assertEqual(self.desktop.effects,[])
        self.assertNotEqual(result.outcome,'entry_confirmation_required')

    def test_rejected_launch_is_reconsidered_by_model_then_confirmed_without_executing_subset(self):
        args=proposal(reference='there',launch=True)
        base=Model(args)
        first=True
        def choose(messages,**options):
            nonlocal first
            if first:
                first=False
                return iter(fixture.event('open_application',{'app_id':'notepad'}))
            return base(messages,**options)
        engine=ConversationController(Mock(stream_chat=Mock(side_effect=choose)),self.conversation,
            model=DEFAULT_CHAT_MODEL,settings=GenerationSettings(temperature=.2))
        initial=engine.respond('open Notepad and put Hello in there.')
        self.assertEqual(initial.outcome,'entry_confirmation_required')
        self.assertEqual(initial.task.decisions,2)
        self.assertEqual(self.desktop.effects,[])
        result=engine.respond('Yes.')
        self.assertEqual(result.outcome,'text_entry_completed')
        self.assertEqual((result.task.decisions,len(result.task.operations)),(6,5))

    def test_incomplete_sequence_is_repaired_once_without_changing_literal(self):
        model=Model(proposal(reference='there',launch=True))
        first=True
        def choose(messages,**options):
            nonlocal first
            events=list(model(messages,**options))
            if first:
                first=False
                call=deepcopy(events[-1].tool_calls[0]);call['function']['arguments']['objective']['launch_requested']=False
                return iter(fixture.event('request_text_entry',call['function']['arguments']))
            return iter(events)
        engine=ConversationController(Mock(stream_chat=Mock(side_effect=choose)),self.conversation,
            model=DEFAULT_CHAT_MODEL,settings=GenerationSettings(temperature=.2))
        self.assertEqual(engine.respond('open Notepad and put Hello in there.').outcome,'entry_confirmation_required')
        self.assertEqual(self.desktop.effects,[])
        self.assertEqual(engine.respond('Yes.').outcome,'text_entry_completed')

    def test_review_cannot_change_literal_or_repeat_forever(self):
        model=Model(proposal())
        count=0
        def choose(messages,**options):
            nonlocal count
            count+=1
            if count>1:model.args['text']=model.args['objective']['text_evidence']='Different'
            return model(messages,**options)
        engine=ConversationController(Mock(stream_chat=Mock(side_effect=choose)),self.conversation,
            model=DEFAULT_CHAT_MODEL,settings=GenerationSettings(temperature=.2))
        result=engine.respond('open Notepad and put Hello in there.')
        self.assertEqual(result.outcome,'denied')
        self.assertEqual(count,2)
        self.assertEqual(self.desktop.effects,[])
        self.assertIsNone(objective.pending_for(self.conversation))

    def test_slash_clear_forgets_pending_literal_and_history(self):
        from aqua.chat import _control_command
        engine=self.engine(proposal());engine.respond('Put Hello in Notepad.')
        self.assertIsNotNone(objective.pending_for(self.conversation))
        _control_command('/clear',self.conversation,voice=False)
        self.assertFalse(hasattr(self.conversation,'_entry_consent'))
        self.assertEqual(self.conversation.history,[])

    def test_launching_instead_of_confirming_does_not_retain_an_old_write(self):
        from collections import deque
        engine=self.engine(proposal());engine.respond('Put Hello in Notepad.')
        replies=deque([fixture.event('open_application',{'app_id':'notepad'}),speech.native_reply('Launch reported.')])
        engine.client.stream_chat.side_effect=lambda *_a,**_k:iter(replies.popleft())
        engine.respond('Open Notepad.')
        self.assertEqual(engine.respond('Yes.').outcome,'stale_confirmation')
        self.assertEqual(self.desktop.effects,['launch:notepad'])

    def test_affirmative_introduction_is_a_new_objective_not_stale_consent(self):
        args=proposal('hello',reference='there',launch=True)
        model=Model(args)
        first=True
        def choose(messages,**options):
            nonlocal first
            if first:
                first=False
                return iter(fixture.event('open_application',{'app_id':'notepad'}))
            return model(messages,**options)
        engine=ConversationController(Mock(stream_chat=Mock(side_effect=choose)),self.conversation,
            model=DEFAULT_CHAT_MODEL,settings=GenerationSettings(temperature=.2))
        self.assertEqual(engine.respond('Yes, open Notepad and write hello in there.').outcome,'entry_confirmation_required')
        self.assertEqual(self.desktop.effects,[])
        self.assertEqual(engine.respond('Yes.').outcome,'text_entry_completed')
