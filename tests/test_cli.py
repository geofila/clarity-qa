import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from filelock import FileLock
from openai import OpenAI

from clarity_qa.cli import main
from clarity_qa.models import BatchResult
from test_pipeline import GREEK, responses


class CommandLineTests(unittest.TestCase):
    def test_json_file_to_real_sdk_transport_to_label_studio_and_resume(self):
        values = responses("el")
        calls = []

        def handler(request):
            self.assertEqual(request.url.path, "/v1/responses")
            payload = json.loads(request.content)
            calls.append(payload)
            answer = values[len(calls) - 1]
            return httpx.Response(200, json={
                "id": "resp-test", "object": "response", "created_at": 0, "model": "fixture-model",
                "status": "completed", "output": [{
                    "id": "msg-test", "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps(answer, ensure_ascii=False), "annotations": []}],
                }],
            })

        client = OpenAI(
            api_key="test-key", base_url="https://example.invalid/v1", max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"
            output = root / "results.json"
            source.write_text(json.dumps(GREEK, ensure_ascii=False), encoding="utf-8")
            args = ["analyze", "--model", "fixture-model", "--input", str(source), "--output", str(output)]
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("clarity_qa.providers.OpenAI", return_value=client):
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(args), 0)
            self.assertEqual(len(calls), 3)
            report = BatchResult.model_validate_json(output.read_text(encoding="utf-8"))
            self.assertEqual(report.results[0].language, "el")
            self.assertEqual(report.results[0].sub_questions[1].evasion_label.value, "Claims ignorance")
            with patch("clarity_qa.providers.OpenAI") as constructor, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(args + ["--resume"]), 0)
                constructor.assert_not_called()
            tasks = root / "tasks.json"
            config = root / "config.xml"
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["export-label-studio", "--input", str(output), "--output", str(tasks), "--config", str(config)]), 0)
            self.assertEqual(len(json.loads(tasks.read_text())), 2)
            self.assertIn("Additional missed questions", config.read_text())
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(args), 2)

    def test_same_input_output_is_rejected_without_modifying_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            original = json.dumps(GREEK)
            path.write_text(original)
            with contextlib.redirect_stderr(io.StringIO()):
                status = main(["analyze", "--model", "fixture-model", "--input", str(path), "--output", str(path)])
            self.assertEqual(status, 2)
            self.assertEqual(path.read_text(), original)

    def test_invalid_later_input_fails_before_any_model_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.json"
            source.write_text(json.dumps([GREEK, {"question": "Where?", "answer": ""}]))
            with patch("clarity_qa.providers.OpenAI") as constructor, contextlib.redirect_stderr(io.StringIO()):
                status = main(["analyze", "--model", "fixture-model", "--input", str(source), "--output", str(Path(directory) / "out.json")])
            self.assertEqual(status, 2)
            constructor.assert_not_called()

    def test_concurrent_output_writer_is_rejected_before_model_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.json"
            with FileLock(str(output.resolve()) + ".lock"):
                with patch("clarity_qa.providers.OpenAI") as constructor, contextlib.redirect_stderr(io.StringIO()):
                    status = main([
                        "analyze", "--model", "fixture-model", "--question", "When?", "--answer", "Tomorrow.",
                        "--output", str(output),
                    ])
                self.assertEqual(status, 2)
                constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
