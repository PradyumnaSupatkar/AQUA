"""look_up (Wikipedia): privacy guard, API client, recency, reply review, --no-lookup.

No network: the Wikipedia client gets a fake fetch function.
"""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from io import StringIO
import json
import unittest
from unittest.mock import patch

from aqua import cli
from aqua.agent.context import ToolContext
from aqua.agent.policy import DENY, Policy
from aqua.agent.recency import beyond_model_memory, mentions_recent
from aqua.agent.registry import ToolOutcome
from aqua.tools import build_registry
from aqua.tools.app_index import AppIndex
from aqua.tools.fake import FakeDesktop
from aqua.tools.knowledge import (MAX_EXTRACT_CHARS, WikipediaClient, make_look_up, may_be_out_of_date,
                                  privacy_problem, review_reply, shorten, title_match, user_agent)

from action_core_helpers import ScriptedModel, call, make_engine

NOW = datetime(2026, 11, 22, 10, 0)
HAMILTON = ("Sir Lewis Carl Davidson Hamilton is a British racing driver. He has won a joint-record seven "
            "Formula One World Drivers' Championship titles.")
UPCOMING = ("The 2026 Las Vegas Grand Prix is a Formula One motor race that will be held on November 21, 2026. "
            "Max Verstappen was promoted to pole position.")


class FakeWiki:
    """Plays the API: list=search returns titles; titles= returns one page."""

    def __init__(self, pages, search=None):
        self.pages, self.search = pages, search
        self.calls = []

    def __call__(self, params):
        self.calls.append(dict(params))
        if params.get("list") == "search":
            titles = self.search if self.search is not None else list(self.pages)
            return {"query": {"search": [{"title": t} for t in titles]}}
        title = params["titles"]
        if title not in self.pages:
            return {"query": {"pages": [{"title": title, "missing": True}]}}
        return {"query": {"pages": [{"title": title, "extract": self.pages[title],
                                     "fullurl": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                                     "revisions": [{"timestamp": "2026-11-19T10:00:00Z"}]}]}}


def context():
    desktop = FakeDesktop()
    return ToolContext(desktop=desktop, apps=AppIndex(desktop.app_entries, clock=desktop.monotonic), policy=Policy())


class PrivacyGuardTests(unittest.TestCase):
    def test_short_topics_pass(self):
        ctx = context()
        for query in ("Lewis Hamilton", "2026 Las Vegas Grand Prix", "population of Japan", "Eiffel Tower history"):
            self.assertIsNone(privacy_problem(query, ctx), query)

    def test_unsafe_queries_are_refused_with_a_reason(self):
        ctx = context()
        cases = {
            "one two three four five six seven eight nine ten eleven twelve thirteen": "longer than 12 words",
            "what is https://intranet.example/report": "web address", "www.mybank.com login": "web address",
            "mail from sam.lee@example.com": "e-mail", r"C:\Users\Sam\Documents\plan.docx": "file path",
            "contents of plan.docx": "file path", "D: drive files": "file path", "~/notes/today": "file path",
        }
        for query, reason in cases.items():
            problem = privacy_problem(query, ctx)
            self.assertIsNotNone(problem, query)
            self.assertIn(reason, problem, query)

    def test_observed_titles_app_names_and_typed_text_are_refused(self):
        ctx = context()
        ctx.observe("Q3 layoffs draft - Microsoft Word", "brave")
        ctx.typed.append("my secret project Nightingale")
        self.assertIn("window title", privacy_problem("Q3 layoffs draft", ctx))
        self.assertIn("app name", privacy_problem("brave browser history", ctx))
        self.assertIn("type", privacy_problem("project Nightingale", ctx))
        self.assertIn("type", privacy_problem("my secret project Nightingale details", ctx))
        self.assertIsNone(privacy_problem("Lewis Hamilton", ctx))

    def test_engine_records_what_it_saw_and_blocks_the_query_before_any_request(self):
        fetch = FakeWiki({"Lewis Hamilton": HAMILTON})
        desktop = FakeDesktop()
        desktop.add_window("Salary review 2026 - Excel", "excel.exe")
        engine, _, model = make_engine(
            [call("list_windows")], "Excel is open.",
            [call("look_up", query="Salary review 2026")], "I can't look that up.",
            desktop=desktop, registry=build_registry(wikipedia=WikipediaClient(fetch)))
        engine.respond("what windows are open")
        result = engine.respond("who wrote salary review 2026")
        self.assertEqual(fetch.calls, [])
        self.assertEqual(result.task.operations[0].status, "denied")
        sent_back = json.loads(model.tool_results()[-1]["content"])
        self.assertIn("Not sent to Wikipedia", sent_back["message"])

    def test_typed_text_never_becomes_a_query(self):
        fetch = FakeWiki({})
        desktop = FakeDesktop()
        desktop.add_window("Untitled - Notepad", "notepad.exe")
        engine, _, _ = make_engine(
            [call("type_text", text="Project Bluebird budget", app="notepad")], "Typed it.",
            [call("look_up", query="Project Bluebird budget")], "I won't look that up.",
            desktop=desktop, registry=build_registry(wikipedia=WikipediaClient(fetch)))
        engine.respond("write Project Bluebird budget in notepad")
        engine.respond("what is Project Bluebird budget")
        self.assertEqual(fetch.calls, [])


class WikipediaClientTests(unittest.TestCase):
    def test_only_the_query_is_sent_with_a_generic_user_agent(self):
        fetch = FakeWiki({"Lewis Hamilton": HAMILTON})
        page = WikipediaClient(fetch).lookup("Lewis Hamilton world championships")
        self.assertEqual(page["title"], "Lewis Hamilton")
        self.assertEqual(page["last_edited"], "2026-11-19")
        self.assertTrue(page["url"].startswith("https://en.wikipedia.org/wiki/"))
        sent = [value for call_ in fetch.calls for key, value in call_.items() if key in {"srsearch", "titles"}]
        self.assertTrue(all(v in {"Lewis Hamilton world championships", "Lewis Hamilton",
                                  "Lewis Hamilton world championships".replace(" world", "")} for v in sent), sent)
        agent = user_agent()
        self.assertTrue(agent.startswith("AquaVoiceAssistant/"))
        self.assertNotIn("@", agent)
        with patch.dict("os.environ", {"AQUA_WIKI_CONTACT": "https://example.org/contact"}):
            self.assertIn("https://example.org/contact", user_agent())

    def test_named_article_beats_a_broader_search_hit(self):
        fetch = FakeWiki({"Las Vegas Grand Prix": "The Las Vegas Grand Prix is a race.",
                          "2025 Las Vegas Grand Prix": "The 2025 Las Vegas Grand Prix was won by Max Verstappen."},
                         search=["Las Vegas Grand Prix"])
        page = WikipediaClient(fetch).lookup("2025 Las Vegas Grand Prix winner")
        self.assertEqual(page["title"], "2025 Las Vegas Grand Prix")

    def test_search_order_prefers_titles_contained_in_the_query(self):
        fetch = FakeWiki({"List of career achievements by Lewis Hamilton": "A list.", "Lewis Hamilton": HAMILTON},
                         search=["List of career achievements by Lewis Hamilton", "Lewis Hamilton"])
        self.assertEqual(WikipediaClient(fetch).lookup("Lewis Hamilton titles")["title"], "Lewis Hamilton")
        self.assertEqual(title_match("Lewis Hamilton", "lewis hamilton titles"), 2)
        self.assertEqual(title_match("Nicolas Hamilton", "lewis hamilton titles"), 0)

    def test_disambiguation_and_missing_pages_are_skipped(self):
        fetch = FakeWiki({"Mercury": "Mercury may refer to: a planet, an element...", "Mercury (planet)": "A planet."},
                         search=["Mercury", "Mercury (planet)"])
        self.assertEqual(WikipediaClient(fetch).lookup("Mercury")["title"], "Mercury (planet)")
        self.assertIsNone(WikipediaClient(FakeWiki({}, search=[])).lookup("zzzz qqqq"))

    def test_extract_is_shortened_at_a_sentence_and_headings_dropped(self):
        text = "First sentence. " * 200 + "== Background == more"
        short = shorten(text)
        self.assertLessEqual(len(short), MAX_EXTRACT_CHARS)
        self.assertTrue(short.endswith("."))
        self.assertEqual(shorten("Lead text. == Background == Details."), "Lead text. Details.")


class LookUpToolTests(unittest.TestCase):
    def tool(self, pages, **options):
        return make_look_up(WikipediaClient(FakeWiki(pages, **options)), now=lambda: NOW)

    def test_result_carries_source_title_url_and_capped_extract(self):
        ctx = context()
        outcome = self.tool({"Lewis Hamilton": HAMILTON}).run({"query": "Lewis Hamilton"}, ctx)
        self.assertEqual(outcome.status, "ok")
        self.assertEqual((outcome.data["source"], outcome.data["title"]), ("Wikipedia", "Lewis Hamilton"))
        self.assertIn("seven", outcome.data["extract"])
        self.assertIn("According to Wikipedia", outcome.message)
        self.assertNotIn("out of date", outcome.message)

    def test_recent_and_upcoming_events_are_flagged(self):
        ctx = context()
        ctx.begin_request("Who won the 2026 Las Vegas Grand Prix?")
        outcome = self.tool({"2026 Las Vegas Grand Prix": UPCOMING}).run(
            {"query": "2026 Las Vegas Grand Prix winner"}, ctx)
        self.assertTrue(outcome.data["upcoming"])
        self.assertIn("does not have the result yet", outcome.message)
        ctx.begin_request("who is the current president of France")
        recent = self.tool({"President of France": "The president is Emmanuel Macron."}).run(
            {"query": "President of France"}, ctx)
        self.assertIn("out of date", recent.message)

    def test_missing_article_and_network_errors_say_so(self):
        ctx = context()
        self.assertEqual(self.tool({}, search=[]).run({"query": "Nothing here"}, ctx).status, "empty")
        broken = make_look_up(WikipediaClient(lambda params: (_ for _ in ()).throw(OSError("offline"))))
        outcome = broken.run({"query": "Lewis Hamilton"}, ctx)
        self.assertEqual(outcome.status, "failed")
        self.assertIn("do not answer from memory", outcome.message)

    def test_review_replaces_a_guessed_result(self):
        data = {"title": "2026 Las Vegas Grand Prix", "extract": UPCOMING, "upcoming": True, "asks_result": True}
        fixed = review_reply("According to Wikipedia, Max Verstappen won it.", ToolOutcome("ok", "", data=data))
        self.assertIn("has no result yet", fixed)
        self.assertIn("out of date", fixed)
        settled = {"title": "2025 Las Vegas Grand Prix", "extract": "It was won by Max Verstappen."}
        self.assertIsNone(review_reply("Max Verstappen won.", ToolOutcome("ok", "", data=settled)))

    def test_recency_helpers(self):
        self.assertTrue(mentions_recent("who won the 2026 race", NOW))
        self.assertTrue(mentions_recent("the latest iPhone", NOW))
        self.assertFalse(mentions_recent("who won the 2008 race", NOW))
        self.assertTrue(beyond_model_memory("who won the 2025 race", "", NOW))
        self.assertTrue(beyond_model_memory("who won the race", "It has not happened yet.", NOW))
        self.assertFalse(beyond_model_memory("capital of Australia", "Canberra.", NOW))
        self.assertTrue(may_be_out_of_date("Lewis Hamilton", UPCOMING, NOW))


class LookUpEngineTests(unittest.TestCase):
    def engine(self, *rounds, pages=None, search=None):
        fetch = FakeWiki(pages or {}, search=search)
        engine, desktop, model = make_engine(*rounds, registry=build_registry(
            wikipedia=WikipediaClient(fetch)))
        return engine, model, fetch

    def test_answer_keeps_the_source_and_one_caveat(self):
        engine, model, _ = self.engine(
            [call("look_up", query="Lewis Hamilton")],
            "According to Wikipedia, Lewis Hamilton has seven titles. The article may be out of date. Extra.",
            pages={"Lewis Hamilton": HAMILTON})
        result = engine.respond("How many world championships does Lewis Hamilton have?")
        self.assertEqual(result.text, "According to Wikipedia, Lewis Hamilton has seven titles. "
                                      "The article may be out of date.")
        self.assertIn("look_up", model.requests[0]["tools"])

    def test_recent_question_answered_from_memory_is_retried_with_only_look_up(self):
        engine, model, fetch = self.engine(
            "It has not happened yet.", [call("look_up", query="2026 Las Vegas Grand Prix")],
            "According to Wikipedia, Max Verstappen won it.",
            pages={"2026 Las Vegas Grand Prix": UPCOMING})
        result = engine.respond("Who won the 2026 Las Vegas Grand Prix?")
        self.assertEqual(model.requests[1]["tools"], ["look_up"])
        self.assertIn("today is Sunday 22 November 2026", model.requests[1]["messages"][-1]["content"])
        self.assertTrue(fetch.calls)
        self.assertEqual(result.text, "According to Wikipedia, the 2026 Las Vegas Grand Prix has no result yet; "
                                      "the article may be out of date, so I don't want to guess.")

    def test_refusing_to_look_up_a_recent_event_gives_an_honest_reply(self):
        engine, _, _ = self.engine("It has not happened yet.", "It is in the future.")
        result = engine.respond("Who won the 2026 Las Vegas Grand Prix?")
        self.assertIn("may be too recent", result.text)

    def test_unchecked_wikipedia_citation_is_retried_then_removed(self):
        engine, model, _ = self.engine("According to Wikipedia, the capital of Australia is Canberra.",
                                       "According to Wikipedia, it is Canberra.")
        result = engine.respond("What is the capital of Australia?")
        self.assertIn("you have not checked Wikipedia", model.requests[1]["messages"][-1]["content"])
        self.assertEqual(result.text, "It is Canberra.")

    def test_no_lookup_registry_has_no_network_tool(self):
        engine, _, model = make_engine("Canberra.", registry=build_registry(lookup=False))
        engine.respond("What is the capital of Australia?")
        self.assertNotIn("look_up", model.requests[0]["tools"])


class PermissionNudgeTests(unittest.TestCase):
    def test_model_asking_for_permission_is_told_to_call_the_tool(self):
        engine, desktop, model = make_engine("Would you like me to restart the computer?",
                                             [call("power", action="restart")])
        result = engine.respond("restart the computer")
        self.assertIn("Do not ask for permission yourself", model.requests[1]["messages"][-1]["content"])
        self.assertEqual((result.outcome, result.text), ("confirm", "Restart the computer? Say yes or no."))

    def test_false_cannot_is_retried(self):
        engine, desktop, _ = make_engine("I cannot change the brightness.", [call("brightness", action="up")], "Brighter.")
        engine.respond("make the screen brighter")
        self.assertEqual(desktop.brightness, 80)

    def test_plain_questions_are_not_nudged(self):
        engine, _, model = make_engine("Would you like a joke?")
        self.assertEqual(engine.respond("tell me something fun").text, "Would you like a joke?")
        self.assertEqual(len(model.requests), 1)


class NoLookupFlagTests(unittest.TestCase):
    def invoke(self, argv, target):
        with patch(f"aqua.cli.{target}", return_value=0) as run, redirect_stdout(StringIO()), \
                redirect_stderr(StringIO()):
            cli.main(argv)
        return run.call_args.kwargs

    def test_no_lookup_reaches_the_new_engine_only(self):
        for command, target in (("chat", "run_text_chat"), ("voice-chat", "run_voice_chat"), ("handsfree", "run_handsfree")):
            self.assertIs(self.invoke([command, "--engine", "new", "--no-lookup"], target)["lookup"], False)
            self.assertNotIn("lookup", self.invoke([command, "--engine", "new"], target))
            self.assertNotIn("lookup", self.invoke([command, "--no-lookup"], target))

    def test_chat_passes_it_to_the_engine_builder(self):
        from aqua import chat
        from aqua.brain.ollama import DEFAULT_CHAT_MODEL, ModelInfo
        from unittest.mock import Mock
        client = Mock()
        client.check_model.return_value = ModelInfo(DEFAULT_CHAT_MODEL, "t", "qwen3", "non_thinking")
        engine, _, _ = make_engine()
        with patch("aqua.chat.OllamaClient", return_value=client), \
                patch("aqua.agent.loop.build_engine", return_value=engine) as build, \
                patch("builtins.input", side_effect=["/quit"]), redirect_stdout(StringIO()):
            chat.run_text_chat(engine="new", lookup=False)
        self.assertIs(build.call_args.kwargs["lookup"], False)


if __name__ == "__main__":
    unittest.main()
