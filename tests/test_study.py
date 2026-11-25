import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET

import httpx
from openai import OpenAI
from pydantic import ValidationError

from clarity_qa import QAPipeline
from clarity_qa.cli import main
from clarity_qa.io import atomic_json, fingerprint
from clarity_qa.providers import GenerationError, ProviderError
from clarity_qa.study import export_study, make_plan, prepare_study, read_json
from clarity_qa.study_evaluation import evaluate_study, fleiss_kappa
from clarity_qa.study_models import StudyConfig
from clarity_qa.taxonomy import EvasionLabel
from test_pipeline import ENGLISH, GREEK, FixtureBackend, responses


def analyses(n=20, language="en", parts=2):
    base = QAPipeline(backend=FixtureBackend(responses(language))).analyze(GREEK if language == "el" else ENGLISH)
    result = []
    for i in range(n):
        item = base.model_copy(deep=True)
        item.id = i
        item.question += " [Record {}]".format(i)
        item.sub_questions = item.sub_questions[:parts]
        item.metadata = {"gold_label": "private-secret", "is_counterfactual": "private-secret"}
        result.append(item)
    return result


class ControlBackend(FixtureBackend):
    def __init__(self, fail_after=None):
        super().__init__([])
        self.fail_after = fail_after

    def generate(self, system, user, schema):
        payload = json.loads(user.split("\nThe previous attempt")[0])
        self.requests.append((system, payload, schema))
        if self.fail_after is not None and len(self.requests) > self.fail_after:
            raise ProviderError("Fixture outage")
        text = ("Η κατασκευή του νοσοκομείου δεν πρόκειται να ξεκινήσει φέτος."
                if payload["language"] == "el" else "The minister declines to answer the question.")
        self.output_modes.append("fixture")
        return {"misleading_summary": text}


def configuration(controls=0, **extra):
    return StudyConfig.model_validate({
        "annotators": 3, "seed": 42,
        "counterfactuals": {"enabled": bool(controls), "count_per_annotator": controls},
        **extra,
    })


def approve(directory):
    path = Path(directory) / "private/counterfactuals.json"
    rows = read_json(path)
    for row in rows:
        row["review"] = {"status": "approved", "reviewer": "Fixture reviewer",
                         "reference_label": row["proposed_source_label"], "notes": "Synthetic test fixture."}
    atomic_json(path, rows)


def annotation(label="Explicit", summary="Supported", validity="Supported", user=1):
    def choice(name, target, value):
        return {"from_name": name, "to_name": target, "type": "choices", "value": {"choices": [value]}}
    values = [choice("question_validity", "sub_question", validity)]
    if validity == "Supported":
        values += [choice("summary_faithfulness", "sub_answer", summary), choice("evasion", "sub_question", label)]
        if summary == "Unsupported":
            values.append({"from_name": "corrected_summary", "to_name": "sub_answer", "type": "textarea",
                           "value": {"text": ["The source answer gives the original information."]}})
    return {"completed_by": user, "was_cancelled": False, "result": values}


def write_exports(pack, target, manifest, customize=None):
    target = Path(target)
    target.mkdir()
    for index, name in enumerate(manifest["annotators"]):
        tasks = read_json(Path(pack) / (name + ".json"))
        for task in tasks:
            key = task["data"]["task_key"]
            task["annotations"] = [annotation(user=index + 1)]
            if customize:
                customize(task, name, key)
        atomic_json(target / (name + ".json"), tasks)


class StudyAssignmentTests(unittest.TestCase):
    def test_grouped_overlap_covers_every_part_and_balances_work(self):
        items = analyses(100)
        items[0].sub_questions = items[0].sub_questions[:1]
        plan = make_plan(items, configuration(), "study")
        self.assertEqual(len(plan["shared_source_keys"]), 20)
        seen = Counter(key for keys in plan["assignments"].values() for key in keys)
        for key in plan["sources"]:
            self.assertEqual(seen[key], 3 if key in plan["shared_source_keys"] else 1)
        loads = [sum(len(plan["sources"][key]["sub_questions"]) for key in keys) for keys in plan["assignments"].values()]
        self.assertLessEqual(max(loads) - min(loads), 2)
        self.assertEqual(plan, make_plan(items, configuration(), "study"))
        different = make_plan(items, configuration(seed=43), "study")
        self.assertNotEqual(plan["shared_source_keys"], different["shared_source_keys"])

    def test_three_thousand_unit_example_has_1400_normal_tasks_each(self):
        plan = make_plan(analyses(3000, parts=1), configuration(30), "study")
        self.assertEqual(len(plan["shared_source_keys"]), 600)
        self.assertEqual([len(keys) for keys in plan["assignments"].values()], [1400] * 3)
        self.assertEqual(len(plan["counterfactual_slots"]), 90)

    def test_controls_never_repeat_a_source_seen_by_their_recipient(self):
        plan = make_plan(analyses(30), configuration(5), "study")
        seen = set()
        for slot in plan["counterfactual_slots"]:
            self.assertNotIn(slot["source_key"], plan["assignments"][slot["assigned_annotator"]])
            self.assertNotIn(slot["source_key"], plan["shared_source_keys"])
            pair = (slot["assigned_annotator"], slot["source_key"])
            self.assertNotIn(pair, seen)
            seen.add(pair)

    def test_infeasible_controls_fail_before_generation(self):
        backend = ControlBackend()
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "unseen source"):
                prepare_study([item.model_dump(mode="json") for item in analyses(5)], configuration(100),
                              Path(temp) / "study", pipeline=QAPipeline(backend=backend))
        self.assertEqual(backend.requests, [])

    def test_impossible_raw_input_fails_before_any_analysis_requests(self):
        backend = ControlBackend(fail_after=0)
        source = [{**ENGLISH, "id": i, "question": ENGLISH["question"] + str(i)} for i in range(10)]
        with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(ValueError, "unseen source"):
            prepare_study(source, configuration(30), Path(temp) / "study", pipeline=QAPipeline(backend=backend))
        self.assertEqual(backend.requests, [])

    def test_work_balancing_reserves_enough_unseen_sources_for_controls(self):
        items = analyses()
        original = make_plan(items, configuration(), "study")
        private_key = next(key for key in original["sources"] if key not in original["shared_source_keys"])
        heavy = items[original["sources"][private_key]["id"]]
        heavy.sub_questions = [heavy.sub_questions[0].model_copy(update={"id": i + 1}) for i in range(50)]
        plan = make_plan(items, configuration(10), "study")
        self.assertEqual(len(plan["counterfactual_slots"]), 30)
        self.assertTrue(all(len(keys) - 4 <= 6 for keys in plan["assignments"].values()))

    def test_duplicates_mixed_languages_and_empty_groups_are_rejected(self):
        cases = []
        items = analyses(5)
        items[1] = items[0].model_copy(deep=True)
        cases.append(items)
        items = analyses(5)
        items[1].id = items[0].id
        cases.append(items)
        items = analyses(5)
        items[1].language = "el"
        cases.append(items)
        items = analyses(5)
        items[1].sub_questions = []
        cases.append(items)
        for items in cases:
            with self.subTest(items=items[1].id), self.assertRaises(ValueError):
                make_plan(items, configuration(), "study")

    def test_configuration_rejects_unsafe_ids_invalid_counts_and_predictions(self):
        for value in [
            {"annotators": 0}, {"annotators": True}, {"annotators": ["../private"]},
            {"annotators": ["a", "a"]}, {"show_model_labels": True},
            {"overlap": {"fraction": 1.1}}, {"overlap": {"fraction": float("nan")}},
            {"counterfactuals": {"enabled": False, "count_per_annotator": 2}},
            {"counterfactuals": {"count_per_annotator": -1}},
            {"counterfactuals": {"min_length_ratio": 3, "max_length_ratio": 1}},
            {"unknown_option": 1},
        ]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                StudyConfig.model_validate(value)


class StudyFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.study = self.root / "study"
        self.pack = self.root / "pack"

    def prepare(self, controls=2, language="en", **config):
        backend = ControlBackend()
        source = [item.model_dump(mode="json") for item in analyses(20, language)]
        plan = prepare_study(source, configuration(controls, **config), self.study, pipeline=QAPipeline(backend=backend))
        return source, plan, backend


class StudyWorkflowTests(StudyFixture, unittest.TestCase):
    def test_review_gate_blinding_opaque_ids_and_exact_shared_content(self):
        _, plan, _ = self.prepare()
        with self.assertRaisesRegex(ValueError, "Approve all"):
            export_study(self.study, self.pack)
        self.assertFalse(self.pack.exists())
        approve(self.study)
        manifest = export_study(self.study, self.pack)
        self.assertEqual(len(manifest["controls"]), 6)
        self.assertEqual(set(path.name for path in self.pack.iterdir()),
                         {"annotator_01.json", "annotator_02.json", "annotator_03.json", "IMPORT.md", "labeling_config.xml"})
        shared = {}
        for name in manifest["annotators"]:
            tasks = read_json(self.pack / (name + ".json"))
            for task in tasks:
                self.assertEqual(set(task), {"data"})
                self.assertEqual(set(task["data"]), {"task_key", "language", "question", "answer", "sub_question", "sub_answer", "taxonomy_definitions"})
                self.assertNotIn("private-secret", json.dumps(task))
                self.assertRegex(task["data"]["task_key"], r"^[0-9a-f]{32}$")
                key = task["data"]["task_key"]
                info = manifest["tasks"][key]
                if info["source_key"] in plan["shared_source_keys"]:
                    self.assertEqual(task["data"], shared.setdefault(key, task["data"]))
        self.assertEqual(len(shared), 8)

    def test_greek_source_and_generated_text_survive_export(self):
        _, _, backend = self.prepare(language="el")
        self.assertTrue(all(request[1]["language"] == "el" for request in backend.requests))
        approve(self.study)
        manifest = export_study(self.study, self.pack)
        rows = read_json(self.pack / "annotator_01.json")
        for row in rows:
            self.assertIn("νοσοκομείο", row["data"]["question"])
            if row["data"]["task_key"] in manifest["controls"]:
                self.assertIn("κατασκευή", row["data"]["sub_answer"])

    def test_resume_preserves_reviews_and_never_repeats_completed_calls(self):
        source, plan, backend = self.prepare()
        approve(self.study)
        before = (self.study / "private/counterfactuals.json").read_bytes()
        second = QAPipeline(backend=ControlBackend(fail_after=0))
        resumed = prepare_study(source, configuration(2), self.study, pipeline=second, resume=True)
        self.assertEqual(plan, resumed)
        self.assertEqual(second.backend.requests, [])
        self.assertEqual((self.study / "private/counterfactuals.json").read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "identical input"):
            prepare_study(source, configuration(2, seed=43), self.study, pipeline=second, resume=True)

    def test_failed_generation_checkpoints_and_resumes_remaining_controls(self):
        source = [item.model_dump(mode="json") for item in analyses()]
        with self.assertRaises(ProviderError):
            prepare_study(source, configuration(2), self.study, pipeline=QAPipeline(backend=ControlBackend(fail_after=2)))
        self.assertEqual(len(read_json(self.study / "private/counterfactuals.json")), 2)
        backend = ControlBackend()
        prepare_study(source, configuration(2), self.study, pipeline=QAPipeline(backend=backend), resume=True)
        self.assertEqual(len(backend.requests), 4)

    def test_rejected_incomplete_and_mismatched_controls_block_export(self):
        self.prepare()
        approve(self.study)
        path = self.study / "private/counterfactuals.json"
        original = read_json(path)
        variants = []
        rows = copy.deepcopy(original)
        rows[0]["review"]["status"] = "rejected"
        variants.append(rows)
        variants.append(original[:-1])
        rows = copy.deepcopy(original)
        rows[0]["answer"] = "Changed source"
        variants.append(rows)
        rows = copy.deepcopy(original)
        rows[0]["review"]["reference_label"] = rows[0]["counter_label"]
        variants.append(rows)
        rows = copy.deepcopy(original)
        rows[0]["review"]["reviewer"] = ""
        variants.append(rows)
        variants.append(original + original[:1])
        for rows in variants:
            atomic_json(path, rows)
            with self.subTest(first=rows[0]["review"]), self.assertRaises(ValueError):
                export_study(self.study, self.pack)
            self.assertFalse(self.pack.exists())

    def test_normal_only_preparation_needs_no_model_and_never_overwrites(self):
        source = [item.model_dump(mode="json") for item in analyses()]
        prepare_study(source, configuration(), self.study)
        export_study(self.study, self.pack)
        with self.assertRaisesRegex(ValueError, "already exists"):
            export_study(self.study, self.pack)
        with self.assertRaisesRegex(ValueError, "already exists"):
            prepare_study(source, configuration(), self.study)
        with self.assertRaisesRegex(ValueError, "separate"):
            export_study(self.study, self.study / "private/leak")

    def test_reviewer_can_replace_an_initially_correct_counterlabel(self):
        self.prepare()
        approve(self.study)
        path = self.study / "private/counterfactuals.json"
        rows = read_json(path)
        row = rows[0]
        initial_target = row["counter_label"]
        row["review"]["reference_label"] = initial_target
        row["counter_label"] = next(label.value for label in EvasionLabel
                                    if label.value not in (initial_target, row["proposed_source_label"]))
        row["review"]["notes"] = "Corrected the model's reference and rewrote the control target."
        atomic_json(path, rows)
        manifest = export_study(self.study, self.pack)
        matching = [key for key, value in manifest["tasks"].items() if value.get("candidate_id") == row["candidate_id"]]
        self.assertEqual(len(matching), 1)
        self.assertEqual(manifest["controls"][matching[0]]["reference_label"], initial_target)
        self.assertNotEqual(manifest["controls"][matching[0]]["counter_label"], initial_target)

    def test_model_cannot_return_unchanged_or_out_of_length_summary(self):
        for bad in ["x", responses()[1]["sub_answers"][0]["answer_summary"]]:
            pipeline = QAPipeline(backend=FixtureBackend([{"misleading_summary": bad}] * 2))
            source = [item.model_dump(mode="json") for item in analyses(parts=1)]
            with tempfile.TemporaryDirectory() as temp, self.assertRaises(GenerationError):
                prepare_study(source, configuration(1), Path(temp) / "study", pipeline=pipeline)

    def test_changed_frozen_plan_is_rejected(self):
        self.prepare(0)
        path = self.study / "private/plan.json"
        plan = read_json(path)
        plan["shared_source_keys"] = []
        atomic_json(path, plan)
        with self.assertRaisesRegex(ValueError, "frozen study plan"):
            export_study(self.study, self.pack)


class StudyEvaluationTests(StudyFixture, unittest.TestCase):
    def distribution(self, reviewed=True):
        self.prepare(2, counterfactuals={"enabled": True, "count_per_annotator": 2, "require_review": reviewed})
        if reviewed:
            approve(self.study)
        return export_study(self.study, self.pack)

    def test_controls_do_not_enter_agreement_or_merged_normal_annotations(self):
        manifest = self.distribution()
        shared = [key for key, task in manifest["tasks"].items()
                  if task["kind"] == "normal" and task["source_key"] in manifest["shared_source_keys"]]
        pattern = [["Explicit"] * 3, ["Explicit", "Explicit", "General"],
                   ["General"] * 3, ["General", "General", "Explicit"]]
        def customize(task, name, key):
            user = manifest["annotators"].index(name) + 1
            if key in shared:
                task["annotations"] = [annotation(pattern[shared.index(key) % 4][manifest["annotators"].index(name)], user=user)]
            elif key in manifest["controls"]:
                task["annotations"] = [annotation(manifest["controls"][key]["counter_label"], summary="Unsupported", user=user)]
        exports = self.root / "exports"
        write_exports(self.pack, exports, manifest, customize)
        report = evaluate_study(self.study, exports)
        self.assertAlmostEqual(report["agreement"]["evasion"]["kappa"], 1 / 3)
        self.assertAlmostEqual(report["agreement"]["clarity"]["kappa"], 1 / 3)
        self.assertEqual(len(report["normal_annotations"]), 40)
        self.assertEqual(report["agreement"]["evasion"]["items"], 8)
        for scores in report["counterfactuals"].values():
            self.assertEqual(scores["counterlabel_uptake_rate"], 1)
            self.assertEqual(scores["summary_error_detection_rate"], 1)
            self.assertEqual(scores["reviewed_source_label_accuracy"], 0)

    def test_missing_cancelled_and_unsupported_shared_items_are_reported(self):
        manifest = self.distribution()
        shared = [key for key, task in manifest["tasks"].items() if task["source_key"] in manifest["shared_source_keys"]]
        def customize(task, name, key):
            if name == "annotator_01" and key == shared[0]:
                task["annotations"][0]["was_cancelled"] = True
            if name == "annotator_01" and key == shared[1]:
                task["annotations"] = [annotation(validity="Unsupported")]
        exports = self.root / "exports"
        write_exports(self.pack, exports, manifest, customize)
        report = evaluate_study(self.study, exports)
        self.assertEqual(report["agreement"]["excluded_incomplete"], 1)
        self.assertEqual(report["agreement"]["excluded_invalid_subquestions"], 1)
        self.assertEqual(report["agreement"]["evasion"]["items"], 6)

    def test_unreviewed_controls_are_not_scored_against_model_gold(self):
        manifest = self.distribution(reviewed=False)
        exports = self.root / "exports"
        write_exports(self.pack, exports, manifest)
        report = evaluate_study(self.study, exports)
        self.assertTrue(report["warnings"])
        self.assertTrue(all(scores["reviewed_source_label_accuracy"] is None for scores in report["counterfactuals"].values()))

    def test_modified_unknown_duplicate_or_multiple_user_exports_are_rejected(self):
        manifest = self.distribution()
        exports = self.root / "exports"
        write_exports(self.pack, exports, manifest)
        path = exports / "annotator_01.json"
        original = read_json(path)
        variants = []
        rows = copy.deepcopy(original)
        rows[0]["data"]["answer"] = "Modified"
        variants.append(rows)
        rows = copy.deepcopy(original)
        rows[0]["data"]["task_key"] = "unknown"
        variants.append(rows)
        variants.append(original + original[:1])
        rows = copy.deepcopy(original)
        rows[0]["annotations"] *= 2
        variants.append(rows)
        rows = copy.deepcopy(original)
        rows[0]["annotations"][0]["completed_by"] = 999
        variants.append(rows)
        rows = copy.deepcopy(original)
        rows[0]["annotations"][0]["result"][0]["value"]["choices"] = ["wrong"]
        variants.append(rows)
        for rows in variants:
            atomic_json(path, rows)
            with self.subTest(first=rows[0]["data"]["task_key"]), self.assertRaises(ValueError):
                evaluate_study(self.study, exports)

    def test_kappa_matches_hand_calculation_and_handles_no_variation(self):
        rows = [["a", "a", "a"], ["a", "a", "b"], ["b", "b", "b"], ["b", "b", "a"]]
        self.assertAlmostEqual(fleiss_kappa(rows, ["a", "b"])["kappa"], 1 / 3)
        self.assertIsNone(fleiss_kappa([["a"] * 3] * 2, ["a", "b"])["kappa"])
        self.assertIsNone(fleiss_kappa([], ["a", "b"])["kappa"])


class StudyCLITests(unittest.TestCase):
    def test_full_cli_with_real_sdk_transport_review_export_and_evaluation(self):
        # Schema-constrained model transport is real; provider outputs are controlled fixtures.
        values = []
        for _ in range(10):
            values.extend(responses())
        values.extend([{"misleading_summary": "The minister declines to answer the question."}] * 3)
        calls = []
        def handler(request):
            payload = json.loads(request.content)
            calls.append(payload)
            value = values[len(calls) - 1]
            return httpx.Response(200, json={
                "id": "resp-test", "object": "response", "created_at": 0, "model": "fixture",
                "status": "completed", "output": [{"id": "msg-test", "type": "message", "role": "assistant",
                "status": "completed", "content": [{"type": "output_text", "text": json.dumps(value), "annotations": []}]}],
            })
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = [{**ENGLISH, "id": i, "question": ENGLISH["question"] + " [Record {}]".format(i)} for i in range(10)]
            atomic_json(root / "input.json", raw)
            atomic_json(root / "config.json", configuration(1).model_dump(mode="json"))
            client = OpenAI(api_key="fixture-key", base_url="https://example.invalid/v1", max_retries=0,
                            http_client=httpx.Client(transport=httpx.MockTransport(handler)))
            args = ["prepare-study", "--input", str(root / "input.json"), "--config", str(root / "config.json"),
                    "--output", str(root / "study"), "--model", "fixture"]
            with patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-key"}), patch("clarity_qa.providers.OpenAI", return_value=client), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(args), 0)
            self.assertEqual(len(calls), 33)
            self.assertIn("counter_label", calls[-1]["input"][1]["content"])
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["export-study", "--study", str(root / "study"), "--output", str(root / "pack")]), 2)
                approve(root / "study")
                self.assertEqual(main(["export-study", "--study", str(root / "study"), "--output", str(root / "pack")]), 0)
            manifest_path = next((root / "study/private/distributions").glob("*.json"))
            manifest = read_json(manifest_path)
            write_exports(root / "pack", root / "exports", manifest)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["evaluate-study", "--study", str(root / "study"), "--exports", str(root / "exports"),
                                       "--output", str(root / "report.json")]), 0)
            self.assertEqual(len(read_json(root / "report.json")["normal_annotations"]), 20)
            xml = ET.parse(root / "pack/labeling_config.xml").getroot()
            names = {element.attrib["name"] for element in xml.iter() if "name" in element.attrib}
            self.assertTrue({"summary_faithfulness", "corrected_summary", "evasion"}.issubset(names))


if __name__ == "__main__":
    unittest.main()
