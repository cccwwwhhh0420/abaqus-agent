"""Knowledge retrieval and preflight tests; no LLM calls or CAE launch."""
import ast
import importlib
import json
from pathlib import Path
import re
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

from abaqus_agent.bridge import client
from abaqus_agent.tools.api_precheck import precheck_script
from abaqus_agent.tools.doc_search import CommandDocSearch, KNOWLEDGE_PATH

ROOT = Path(__file__).resolve().parents[1]


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.searcher = CommandDocSearch()
        self.searcher.load()

    def test_all_active_cards_are_english_versioned_and_have_parseable_examples(self):
        self.assertEqual(self.searcher.release, "2025")
        self.assertEqual(self.searcher.python, "3.10")
        for entry in self.searcher.entries:
            with self.subTest(entry=entry["id"]):
                content = entry["summary"] + entry["code"] + " ".join(entry["notes"])
                content += " ".join(rule["reason"] for rule in entry.get("blocked_calls", []))
                self.assertIsNone(re.search(r"[\u4e00-\u9fff]", content))
                self.assertTrue(all("/abaqusv2025/" in source for source in entry["sources"]))
                ast.parse(entry["code"])
                self.assertIsNone(precheck_script(entry["code"]))

    def test_exact_error_and_chinese_workflow_retrieve_correct_cards(self):
        self.assertEqual(self.searcher.retrieve("session.processUpdates", 1)[0]["id"], "refresh")
        self.assertEqual(self.searcher.retrieve("帮我导入这个inp并命名为schwarzd", 1)[0]["id"], "import_model")
        self.assertEqual(self.searcher.retrieve("孤立网格分配截面 SectionAssignment", 1)[0]["id"], "section_assignment")
        result = self.searcher.search("processUpdates", limit=1, full_detail=True)
        self.assertIn("vp.forceRefresh()", result)
        self.assertIn("Sources:", result)

    def test_runtime_context_includes_version_guard_and_budgeted_whole_cards(self):
        context = self.searcher.runtime_context("导入 INP 视口显示", limit=4, max_chars=4500)
        self.assertIn("Abaqus/CAE 2025, Python 3.10", context)
        self.assertIn("session.processUpdates", context)
        self.assertIn("exact model name", context)
        self.assertLessEqual(len(context), 4500)
        self.assertEqual(context.count("```" ) % 2, 0)

    def test_unlisted_api_is_not_claimed_invalid(self):
        self.assertIn("not proof", self.searcher.search("zzzz_unlisted_api_zzzz"))

    def test_loader_rejects_wrong_release(self):
        data = json.loads(KNOWLEDGE_PATH.read_text(encoding="utf-8"))
        data["release"] = "2023"
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as directory:
            path = Path(directory) / "wrong_release.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                CommandDocSearch(path).load()


class PrecheckTests(unittest.TestCase):
    def test_reproduced_failure_is_rejected_before_any_task_publication(self):
        script = "from abaqus import mdb, session\nmdb.Model(name='MustNotExist')\nsession.processUpdates()"
        with patch.object(client, "atomic_json") as publish, patch.object(client, "get_ipc_status") as status:
            ok, output = client.run_py(script, confirm_enter=False)
        self.assertFalse(ok)
        self.assertIn("API_PRECHECK", output)
        self.assertIn("no model changes", output)
        publish.assert_not_called()
        status.assert_not_called()

    def test_known_aliases_are_checked(self):
        scripts = ["s = session\ns.processUpdates()",
                   "from abaqus import session as s\ns.processUpdates()",
                   "import abaqus as abq\nabq.session.processUpdates()",
                   "db = mdb\ndb.openMdb(pathName='example.cae')"]
        for script in scripts:
            with self.subTest(script=script):
                self.assertIn("API_PRECHECK", precheck_script(script))

    def test_comments_strings_shadowed_names_and_valid_unknown_calls_are_allowed(self):
        scripts = ["# session.processUpdates()\nprint('session.processUpdates()')",
                   "def f(session):\n    session.processUpdates()",
                   "session = custom_client\nsession.processUpdates()",
                   "p.some_unlisted_but_potentially_valid_method()",
                   "vp = session.viewports[session.currentViewportName]\nvp.forceRefresh()\nvp.view.fitView()",
                   "from abaqus import openMdb\nmdb = openMdb(pathName='example.cae')"]
        for script in scripts:
            with self.subTest(script=script):
                self.assertIsNone(precheck_script(script))

    def test_gui_imports_and_proven_blocking_job_calls_are_rejected(self):
        scripts = ["from abaqusGui import getAFXApp", "import abaqusGui as gui",
                   "j = mdb.Job(name='job', model='model')\nj.waitForCompletion()",
                   "mdb.jobs['job'].waitForCompletion()"]
        for script in scripts:
            with self.subTest(script=script):
                self.assertIn("API_PRECHECK", precheck_script(script))

    def test_python_syntax_error_is_not_submitted(self):
        self.assertIn("syntax error", precheck_script("if:"))


class AgentReferenceTests(unittest.TestCase):
    def test_reference_is_injected_before_first_llm_call_and_refreshed_next_turn(self):
        core = importlib.import_module("abaqus_agent.agent.core")
        requests = []
        def chat(messages, **kwargs):
            requests.append(list(messages))
            return iter([]), {"message": {"role": "assistant", "content": "done"}}
        agent = core.AbaqusAgent(llm_client=types.SimpleNamespace(chat_stream=chat), console=MagicMock())
        agent.run_turn("导入inp并命名为schwarzd")
        agent.run_turn("密度 Density")
        for request in requests:
            references = [m for m in request if m.get("name") == "abaqus_runtime_reference"]
            self.assertEqual(len(references), 1)
            self.assertIn("Abaqus/CAE 2025", references[0]["content"])
        self.assertIn("mass_density", requests[1][-1]["content"])
        self.assertNotIn("processUpdates", core.ABAQUS_SYSTEM_PROMPT)

    def test_api_rejection_skips_dependent_calls_and_injects_repair_before_retry(self):
        core = importlib.import_module("abaqus_agent.agent.core")
        calls = [{"id": str(i), "type": "function", "function": {
            "name": "abaqus_run_script", "arguments": json.dumps({"script": "session.processUpdates()"})}}
            for i in range(2)]
        requests = []
        def chat(messages, **kwargs):
            requests.append(list(messages))
            message = ({"role": "assistant", "content": None, "tool_calls": calls}
                       if len(requests) == 1 else {"role": "assistant", "content": "replanned"})
            return iter([]), {"message": message}
        agent = core.AbaqusAgent(llm_client=types.SimpleNamespace(chat_stream=chat), console=MagicMock())
        with patch.object(core, "dispatch_tool_call", return_value="ERROR: API_PRECHECK: session.processUpdates()") as dispatch:
            agent.run_turn("display the imported model")
        self.assertEqual(dispatch.call_count, 1)
        results = [message for message in requests[1] if message["role"] == "tool"]
        self.assertEqual(len(results), 2)
        self.assertTrue(results[1]["content"].startswith("NOT_EXECUTED"))
        self.assertIn("API repair reference", requests[1][-1]["content"])
        self.assertIn("vp.forceRefresh()", requests[1][-1]["content"])
