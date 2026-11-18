import copy
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from pydantic import ValidationError

from clarity_qa import QAPipeline
from clarity_qa.io import load_pairs, run_batch
from clarity_qa.label_studio import export_tasks, labeling_config
from clarity_qa.models import Analysis, BatchResult, QAPair
from clarity_qa.providers import GenerationError, ProviderError
from clarity_qa.taxonomy import CLARITY_EXPLANATIONS, TAXONOMY, ClarityLabel, EvasionLabel


ENGLISH = {
    "id": "hospital-en", "language": "en",
    "question": "When will the new hospital open, and what will it cost?",
    "answer": "The hospital will open in June. I do not know the final cost yet.",
}
GREEK = {
    "id": "hospital-el", "language": "el",
    "question": "Πότε θα ανοίξει το νέο νοσοκομείο και πόσο θα κοστίσει;",
    "answer": "Το νοσοκομείο θα ανοίξει τον Ιούνιο. Δεν γνωρίζω ακόμη το τελικό κόστος.",
    "metadata": {"source": "Synthetic example", "speaker": "Minister"},
}


def responses(language="en"):
    if language == "el":
        titles = ["Χρόνος έναρξης", "Τελικό κόστος"]
        questions = ["Πότε θα ανοίξει το νέο νοσοκομείο;", "Πόσο θα κοστίσει το νέο νοσοκομείο;"]
        answers = ["Το νοσοκομείο θα ανοίξει τον Ιούνιο.", "Δεν γνωρίζω ακόμη το τελικό κόστος."]
        question_summary = "Ζητούνται ο χρόνος έναρξης και το κόστος του νέου νοσοκομείου."
    else:
        titles = ["Opening date", "Final cost"]
        questions = ["When will the new hospital open?", "What will the new hospital cost?"]
        answers = ["The hospital will open in June.", "I do not know the final cost yet."]
        question_summary = "The question asks about the hospital's opening date and cost."
    return [
        {
            "language": language,
            "question_summary": question_summary,
            "sub_questions": [
                {"id": index + 1, "title": title, "question": question}
                for index, (title, question) in enumerate(zip(titles, questions))
            ],
        },
        {
            "answer_summary": " ".join(answers),
            "sub_answers": [
                {"id": index + 1, "answer_summary": answer, "evidence": [answer]}
                for index, answer in enumerate(answers)
            ],
        },
        {"predictions": [{"id": 1, "evasion_label": "Explicit"}, {"id": 2, "evasion_label": "Claims ignorance"}]},
    ]


class FixtureBackend:
    provider = "fixture"
    model = "deterministic-example"
    api = "fixture"

    def __init__(self, values):
        self.values = copy.deepcopy(values)
        self.output_modes = []
        self.requests = []

    def generate(self, system, user, schema):
        self.requests.append((system, json.loads(user.split("\nThe previous attempt")[0]), schema))
        if not self.values:
            raise AssertionError("Unexpected model request")
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        self.output_modes.append("fixture")
        return value

    def settings(self):
        return {"provider": self.provider, "model": self.model, "api": self.api}

    def close(self):
        pass


class PipelineTests(unittest.TestCase):
    def test_greek_text_fixed_taxonomy_and_exact_original_evidence(self):
        backend = FixtureBackend(responses("el"))
        result = QAPipeline(backend=backend).analyze(GREEK)
        self.assertEqual(result.question, GREEK["question"])
        self.assertEqual(result.answer, GREEK["answer"])
        self.assertEqual(result.metadata, GREEK["metadata"])
        self.assertEqual(result.language, "el")
        self.assertEqual(result.sub_questions[1].evasion_label, EvasionLabel.IGNORANCE)
        self.assertEqual(result.sub_questions[1].clarity_label, ClarityLabel.CLEAR_NON_REPLY)
        self.assertEqual(result.sub_questions[1].label_explanation, TAXONOMY[EvasionLabel.IGNORANCE][1])
        for part in result.sub_questions:
            for span in part.answer_evidence:
                self.assertEqual(result.answer[span.start:span.end], span.text)
        self.assertEqual(len(backend.requests), 3)
        self.assertEqual(backend.requests[2][1]["answer"], GREEK["answer"])
        self.assertEqual(result.provenance.review_status, "unreviewed")

    def test_single_pair_normal_python_entry_point(self):
        backend = FixtureBackend(responses())
        result = QAPipeline(backend=backend).analyze(ENGLISH["question"], ENGLISH["answer"], language="en", id=7)
        self.assertEqual(result.id, 7)
        self.assertEqual(result.sub_questions[0].question, "When will the new hospital open?")

    def test_language_override_is_passed_through_every_stage(self):
        backend = FixtureBackend(responses("el"))
        result = QAPipeline(backend=backend).analyze({**GREEK, "language": "auto"}, language="el")
        self.assertEqual(result.language, "el")
        self.assertTrue(all(request[1]["language"] == "el" for request in backend.requests))

    def test_detected_language_flows_to_following_stages(self):
        backend = FixtureBackend(responses("el"))
        result = QAPipeline(backend=backend).analyze({**GREEK, "language": "auto"})
        self.assertEqual(result.language, "el")
        self.assertEqual(backend.requests[0][1]["language"], "auto")
        self.assertEqual(backend.requests[1][1]["language"], "el")

    def test_model_cannot_replace_canonical_explanations(self):
        values = responses()
        values[2]["predictions"][0]["label_explanation"] = "Invented definition"
        backend = FixtureBackend(values + [responses()[2]])
        result = QAPipeline(backend=backend).analyze(ENGLISH)
        self.assertEqual(len(backend.requests), 4)
        self.assertEqual(result.sub_questions[0].label_explanation, TAXONOMY[EvasionLabel.EXPLICIT][1])

    def test_fabricated_evidence_is_repaired(self):
        values = responses()
        wrong = copy.deepcopy(values[1])
        wrong["sub_answers"][0]["evidence"] = ["The hospital is already open."]
        backend = FixtureBackend([values[0], wrong, values[1], values[2]])
        result = QAPipeline(backend=backend).analyze(ENGLISH)
        self.assertEqual(len(backend.requests), 4)
        self.assertIn("June", result.sub_questions[0].answer_evidence[0].text)

    def test_reordered_or_duplicate_ids_cannot_attach_wrong_label(self):
        values = responses()
        values[2]["predictions"][1]["id"] = 1
        backend = FixtureBackend(values)
        with self.assertRaisesRegex(GenerationError, "classification failed"):
            QAPipeline(backend=backend, repair_attempts=0).analyze(ENGLISH)

    def test_missing_sub_answer_is_rejected(self):
        values = responses()
        values[1]["sub_answers"].pop()
        with self.assertRaisesRegex(GenerationError, "answer summaries failed"):
            QAPipeline(backend=FixtureBackend(values), repair_attempts=0).analyze(ENGLISH)

    def test_unknown_or_localized_labels_are_rejected(self):
        values = responses()
        values[2]["predictions"][0]["evasion_label"] = "Σαφής απάντηση"
        with self.assertRaises(GenerationError):
            QAPipeline(backend=FixtureBackend(values), repair_attempts=0).analyze(ENGLISH)

    def test_no_question_does_not_invent_labels(self):
        backend = FixtureBackend([
            {"language": "en", "question_summary": "A statement about a hospital.", "sub_questions": []},
            {"answer_summary": "The speaker acknowledges the statement.", "sub_answers": []},
        ])
        result = QAPipeline(backend=backend).analyze("The hospital opens in June.", "That is correct.")
        self.assertEqual(result.sub_questions, [])
        self.assertEqual(len(backend.requests), 2)

    def test_unsupported_fields_empty_text_and_non_string_inputs_fail_before_requests(self):
        backend = FixtureBackend([])
        pipeline = QAPipeline(backend=backend)
        for bad in [
            {"question": " ", "answer": "yes"},
            {"question": "When?", "answer": ""},
            {"question": 123, "answer": "yes"},
            {"question": "When?", "answer": "yes", "source": "unknown field"},
        ]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                pipeline.analyze(bad)
        self.assertEqual(backend.requests, [])

    def test_output_reload_rejects_changed_taxonomy_or_offsets(self):
        result = QAPipeline(backend=FixtureBackend(responses())).analyze(ENGLISH).model_dump(mode="json")
        changed = copy.deepcopy(result)
        changed["sub_questions"][0]["label_explanation"] = "Different"
        with self.assertRaises(ValidationError):
            Analysis.model_validate(changed)
        result["sub_questions"][0]["answer_evidence"][0]["start"] = 2
        with self.assertRaises(ValidationError):
            Analysis.model_validate(result)

    def test_input_shapes(self):
        for value in [ENGLISH, [ENGLISH], {"items": [ENGLISH]}]:
            self.assertEqual(load_pairs(value)[0].id, "hospital-en")
        self.assertEqual(load_pairs([ENGLISH, GREEK], language="auto")[1].language, "auto")
        for bad in [[], {"items": [ENGLISH], "extra": True}, [ENGLISH, "bad"]]:
            with self.assertRaises(ValueError):
                load_pairs(bad)

    def test_failed_batch_checkpoints_and_resume_skips_completed_exchange(self):
        pairs = load_pairs([ENGLISH, GREEK])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "results.json"
            backend = FixtureBackend(responses() + [ProviderError("Temporary outage")])
            with self.assertRaises(ProviderError):
                run_batch(pairs, QAPipeline(backend=backend), output)
            partial = BatchResult.model_validate_json(output.read_text())
            self.assertEqual(len(partial.results), 1)
            self.assertEqual(partial.errors[0].input_index, 1)
            new_backend = FixtureBackend(responses("el"))
            finished = run_batch(pairs, QAPipeline(backend=new_backend), output, resume=True)
            self.assertEqual([item.input_index for item in finished.results], [0, 1])
            self.assertEqual(finished.errors, [])
            self.assertEqual(len(new_backend.requests), 3)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])
            with self.assertRaisesRegex(ValueError, "same inputs"):
                run_batch(list(reversed(pairs)), QAPipeline(backend=FixtureBackend([])), output, resume=True)

    def test_label_studio_predictions_match_config_and_keep_original_context(self):
        result = QAPipeline(backend=FixtureBackend(responses("el"))).analyze(GREEK)
        tasks = export_tasks([result])
        root = ET.fromstring(labeling_config())
        choices = {choice.attrib["value"] for choice in root.findall("./Choices[@name='evasion']/Choice")}
        self.assertEqual(choices, {label.value for label in TAXONOMY})
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[1]["data"]["answer"], GREEK["answer"])
        prediction = tasks[1]["predictions"][0]["result"][0]
        self.assertEqual(prediction["value"]["choices"], ["Claims ignorance"])
        self.assertEqual(prediction["to_name"], "sub_question")

    def test_all_nine_labels_have_one_deterministic_clarity_mapping(self):
        self.assertEqual(len(TAXONOMY), 9)
        self.assertEqual({item[0] for item in TAXONOMY.values()}, set(ClarityLabel))
        for clarity, explanation in TAXONOMY.values():
            self.assertTrue(explanation)
            self.assertTrue(CLARITY_EXPLANATIONS[clarity])


if __name__ == "__main__":
    unittest.main()
