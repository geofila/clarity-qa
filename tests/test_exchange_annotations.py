"""Regression cases for complete exchanges and real Label Studio span records."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from clarity_qa.io import atomic_json, fingerprint
from clarity_qa.label_studio import ERROR_LABEL, exchange_data, export_tasks, utf16_length, utf16_slice
from clarity_qa.models import Analysis
from clarity_qa.study import export_study, normal_data, prepare_study, read_json, task_key
from clarity_qa.study_evaluation import evaluate_study, parse_annotation, parse_highlight_annotation
from test_study import StudyFixture, analyses, annotation, approve, configuration, write_exports


class HighlightParsingTests(unittest.TestCase):
    def setUp(self):
        self.analysis = analyses(1)[0]
        self.analysis.sub_questions[0].answer_summary = "A date 😀 and a qualification are stated."
        self.data, self.parts = exchange_data(self.analysis, "fixture")
        self.completed = annotation(data=self.data)

    def test_unicode_offsets_and_independent_labels_roundtrip(self):
        self.assertGreater(utf16_length(self.data["sub_answers"]), len(self.data["sub_answers"]))
        self.completed["result"][1]["value"]["labels"] = ["Claims ignorance"]
        parsed = parse_highlight_annotation(self.completed, self.data, self.parts)
        self.assertEqual(parsed["parts"]["1"]["evasion_label"], "Explicit")
        self.assertEqual(parsed["parts"]["2"]["evasion_label"], "Claims ignorance")
        self.assertEqual(parsed["parts"]["2"]["clarity_label"], "Clear Non-Reply")
        for span in self.parts:
            self.assertTrue(utf16_slice(self.data["sub_answers"], span["start"], span["end"]).startswith(str(span["number"]) + ". "))
        with self.assertRaises(ValueError):
            utf16_slice("😀", 0, 1)

    def test_error_excludes_only_its_own_question_and_notes_are_preserved(self):
        self.completed["result"][0]["value"]["labels"] = [ERROR_LABEL]
        self.completed["result"].append({"from_name": "corrections", "to_name": "sub_answers", "type": "textarea",
                                          "value": {"text": ["A missing question?: Dodging", "The second summary needs review."]}})
        parsed = parse_highlight_annotation(self.completed, self.data, self.parts)
        self.assertIsNone(parsed["parts"]["1"]["evasion_label"])
        self.assertEqual(parsed["parts"]["2"]["evasion_label"], "Explicit")
        self.assertIn("needs review", parsed["corrections"])

    def test_missing_duplicate_partial_cross_answer_and_invalid_spans_fail(self):
        variants = []
        value = copy.deepcopy(self.completed); value["result"].pop(); variants.append(value)
        value = copy.deepcopy(self.completed); value["result"].append(copy.deepcopy(value["result"][0])); variants.append(value)
        value = copy.deepcopy(self.completed); value["result"][0]["value"]["end"] -= 2; value["result"][0]["value"].pop("text"); variants.append(value)
        value = copy.deepcopy(self.completed); value["result"][0]["value"]["end"] = self.parts[-1]["end"]; value["result"][0]["value"].pop("text"); variants.append(value)
        for bad in [-1, True, "0", 10**9]:
            value = copy.deepcopy(self.completed); value["result"][0]["value"]["start"] = bad; variants.append(value)
        value = copy.deepcopy(self.completed); value["result"][0]["value"]["labels"] = ["Explicit", "Dodging"]; variants.append(value)
        value = copy.deepcopy(self.completed); value["result"][0]["value"]["text"] = "Invented source text"; variants.append(value)
        value = copy.deepcopy(self.completed); value["result"][0]["id"] = []; variants.append(value)
        for index, value in enumerate(variants):
            with self.subTest(case=index), self.assertRaises(ValueError):
                parse_highlight_annotation(value, self.data, self.parts)

    def test_variable_parts_remain_grouped_and_blinded(self):
        values = analyses(3)
        values[0].sub_questions = values[0].sub_questions[:1]
        values[2].sub_questions.append(values[2].sub_questions[-1].model_copy(update={"id": 3}))
        tasks = export_tasks(values)
        self.assertEqual(len(tasks), 3)
        self.assertEqual([len(t["data"]["sub_answers"].split("\n\n")) for t in tasks], [1, 2, 3])
        self.assertTrue(all(set(t) == {"data"} for t in tasks))
        self.assertNotIn("private-secret", json.dumps(tasks))
        self.assertEqual([t["data"]["answer"] for t in tasks], [v.answer for v in values])

    def test_legacy_choice_parser_keeps_summary_corrections(self):
        parsed = parse_annotation(annotation(summary="Unsupported"))
        self.assertEqual(parsed["summary_faithfulness"], "Unsupported")
        self.assertTrue(parsed["corrected_summary"])


class GroupedStudyTests(StudyFixture, unittest.TestCase):
    def test_control_keeps_all_parts_and_changes_exactly_one_summary(self):
        _, plan, _ = self.prepare()
        approve(self.study)
        manifest = export_study(self.study, self.pack)
        for name in manifest["annotators"]:
            for task in read_json(self.pack / (name + ".json")):
                key = task["data"]["task_key"]
                info = manifest["tasks"][key]
                source = Analysis.model_validate(plan["sources"][info["source_key"]])
                original, _ = exchange_data(source, key)
                self.assertEqual(task["data"]["question"], source.question)
                self.assertEqual(task["data"]["answer"], source.answer)
                self.assertEqual(task["data"]["sub_questions"], original["sub_questions"])
                self.assertEqual(len(info["parts"]), len(source.sub_questions))
                if key in manifest["controls"]:
                    before, after = original["sub_answers"].split("\n\n"), task["data"]["sub_answers"].split("\n\n")
                    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
                    self.assertEqual(changed, [manifest["controls"][key]["part_id"] - 1])
                else:
                    self.assertEqual(task["data"], original)

    def test_control_accuracy_uses_only_manipulated_part(self):
        self.prepare(); approve(self.study)
        manifest = export_study(self.study, self.pack)
        def customize(task, name, key):
            if key in manifest["controls"]:
                reference = manifest["controls"][key]
                for region, span in zip(task["annotations"][0]["result"], manifest["tasks"][key]["parts"]):
                    region["value"]["labels"] = [reference["reference_label"] if span["part_id"] == reference["part_id"] else reference["counter_label"]]
        write_exports(self.pack, self.root / "exports", manifest, customize)
        report = evaluate_study(self.study, self.root / "exports")
        self.assertEqual(len(report["normal_annotations"]), 40)
        self.assertEqual(len({row["unit_key"] for row in report["normal_annotations"]}), 40)
        for scores in report["counterfactuals"].values():
            self.assertEqual(scores["reviewed_source_label_accuracy"], 1)
            self.assertEqual(scores["counterlabel_uptake_rate"], 0)
            self.assertEqual(scores["labeled"], 2)

    def test_independent_part_disagreements_have_hand_calculated_kappa(self):
        self.prepare(0)
        manifest = export_study(self.study, self.pack)
        shared = [key for key, info in manifest["tasks"].items() if info["source_key"] in manifest["shared_source_keys"]]
        patterns = [["Explicit"] * 3, ["Explicit", "Explicit", "General"], ["General"] * 3, ["General", "General", "Explicit"]]
        def customize(task, name, key):
            if key in shared:
                for index, region in enumerate(task["annotations"][0]["result"]):
                    region["value"]["labels"] = [patterns[(2 * shared.index(key) + index) % 4][manifest["annotators"].index(name)]]
        write_exports(self.pack, self.root / "exports", manifest, customize)
        report = evaluate_study(self.study, self.root / "exports")
        self.assertEqual(report["agreement"]["evasion"]["items"], 8)
        self.assertAlmostEqual(report["agreement"]["evasion"]["kappa"], 1 / 3)
        self.assertTrue(all(v["completed_subquestions"] == v["assigned_subquestions"] for v in report["completion"].values()))

    def test_legacy_distribution_can_still_be_evaluated(self):
        _, plan, _ = self.prepare(0)
        manifest = {"schema_version": "1.0", "study_id": plan["study_id"], "plan_sha256": fingerprint(plan),
                    "annotators": list(plan["assignments"]), "tasks": {}, "assignments": {}, "controls": {},
                    "shared_source_keys": plan["shared_source_keys"]}
        exports = self.root / "legacy-exports"; exports.mkdir()
        for user, (name, sources) in enumerate(plan["assignments"].items(), 1):
            tasks, keys = [], []
            for source in sources:
                for part in plan["sources"][source]["sub_questions"]:
                    data = normal_data(plan, source, part["id"]); key = data["task_key"]
                    manifest["tasks"][key] = {"kind": "normal", "source_key": source, "part_id": part["id"], "data_sha256": fingerprint(data)}
                    keys.append(key); tasks.append({"data": data, "annotations": [annotation(user=user)]})
            manifest["assignments"][name] = keys
            atomic_json(exports / (name + ".json"), tasks)
        distribution = fingerprint(manifest)[:24]; manifest["distribution_id"] = distribution
        atomic_json(self.study / "private/distributions" / (distribution + ".json"), manifest)
        report = evaluate_study(self.study, exports, distribution)
        self.assertEqual(report["annotation_format"], "legacy-choices-v1")
        self.assertEqual(len(report["normal_annotations"]), 40)
        self.assertEqual(report["agreement"]["evasion"]["items"], 8)


if __name__ == "__main__":
    unittest.main()
