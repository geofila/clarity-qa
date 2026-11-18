import json
import unittest

import httpx
from openai import OpenAI

from clarity_qa.models import LabelPredictions
from clarity_qa.providers import GenerationError, ModelBackend, ProviderError


SCHEMA = LabelPredictions.model_json_schema()
VALUE = {"predictions": [{"id": 1, "evasion_label": "Explicit"}]}


def completion(text=None, finish_reason="stop", refusal=None):
    return {
        "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "fixture-model",
        "choices": [{"index": 0, "finish_reason": finish_reason, "message": {
            "role": "assistant", "content": text if text is not None else json.dumps(VALUE), "refusal": refusal,
        }}],
    }


class TransportTests(unittest.TestCase):
    def backend(self, handler, provider="openrouter", api=None, output_mode="auto"):
        client = OpenAI(
            api_key="test-key", base_url="https://example.invalid/v1", max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        return ModelBackend(model="fixture-model", provider=provider, api=api, output_mode=output_mode, client=client)

    def test_chat_endpoint_and_strict_json_schema(self):
        requests = []

        def handler(request):
            requests.append(json.loads(request.content))
            self.assertEqual(request.url.path, "/v1/chat/completions")
            return httpx.Response(200, json=completion())

        with self.backend(handler) as backend:
            self.assertEqual(backend.generate("system", "{}", SCHEMA), VALUE)
        self.assertTrue(requests[0]["response_format"]["json_schema"]["strict"])
        self.assertIn("max_tokens", requests[0])

    def test_huggingface_uses_chat_without_a_separate_sdk(self):
        paths = []

        def handler(request):
            paths.append(request.url.path)
            return httpx.Response(200, json=completion())

        with self.backend(handler, provider="huggingface") as backend:
            self.assertEqual(backend.generate("system", "{}", SCHEMA), VALUE)
        self.assertEqual(paths, ["/v1/chat/completions"])

    def test_responses_endpoint_schema_and_no_server_storage(self):
        requests = []

        def handler(request):
            requests.append(json.loads(request.content))
            self.assertEqual(request.url.path, "/v1/responses")
            return httpx.Response(200, json={
                "id": "resp-test", "object": "response", "created_at": 0, "model": "fixture-model",
                "status": "completed", "output": [{
                    "id": "msg-test", "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps(VALUE), "annotations": []}],
                }],
            })

        with self.backend(handler, provider="openai") as backend:
            self.assertEqual(backend.generate("system", "{}", SCHEMA), VALUE)
        self.assertFalse(requests[0]["store"])
        self.assertEqual(requests[0]["text"]["format"]["type"], "json_schema")

    def test_only_explicit_unsupported_format_downgrades_and_is_remembered(self):
        modes = []

        def handler(request):
            value = json.loads(request.content)
            mode = value.get("response_format", {}).get("type", "prompt")
            modes.append(mode)
            if mode != "prompt":
                return httpx.Response(400, json={"error": {"message": "response_format {} is not supported".format(mode), "type": "invalid_request_error"}})
            self.assertIn("Return JSON matching this schema", value["messages"][0]["content"])
            return httpx.Response(200, json=completion())

        with self.backend(handler) as backend:
            self.assertEqual(backend.generate("system", "{}", SCHEMA), VALUE)
            self.assertEqual(backend.generate("system", "{}", SCHEMA), VALUE)
            self.assertEqual(backend.output_modes, ["prompt", "prompt"])
        self.assertEqual(modes, ["json_schema", "json_object", "prompt", "prompt"])

    def test_authentication_errors_do_not_trigger_fallback_or_expose_key(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(401, json={"error": {"message": "Bad key test-key", "type": "authentication_error"}})

        with self.backend(handler) as backend:
            with self.assertRaises(ProviderError) as error:
                backend.generate("system", "{}", SCHEMA)
        self.assertNotIn("test-key", str(error.exception))
        self.assertEqual(len(calls), 1)

    def test_unrelated_bad_request_does_not_trigger_format_fallback(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(400, json={"error": {"message": "Context length exceeded", "type": "invalid_request_error"}})

        with self.backend(handler) as backend, self.assertRaises(ProviderError):
            backend.generate("system", "{}", SCHEMA)
        self.assertEqual(len(calls), 1)

    def test_forced_schema_mode_does_not_downgrade(self):
        def handler(request):
            return httpx.Response(400, json={"error": {"message": "json_schema is not supported", "type": "invalid_request_error"}})

        with self.backend(handler, output_mode="json_schema") as backend, self.assertRaises(ProviderError):
            backend.generate("system", "{}", SCHEMA)

    def test_truncated_refused_and_malformed_chat_responses_fail(self):
        for response in [completion(finish_reason="length"), completion(refusal="Refused"), completion(text="{incomplete")]:
            with self.subTest(response=response):
                with self.backend(lambda request: httpx.Response(200, json=response)) as backend:
                    with self.assertRaises(GenerationError):
                        backend.generate("system", "{}", SCHEMA)

    def test_non_object_json_is_rejected(self):
        with self.backend(lambda request: httpx.Response(200, json=completion(text="[]"))) as backend:
            with self.assertRaisesRegex(GenerationError, "JSON object"):
                backend.generate("system", "{}", SCHEMA)


if __name__ == "__main__":
    unittest.main()
