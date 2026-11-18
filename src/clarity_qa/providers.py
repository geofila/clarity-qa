"""OpenAI Responses and OpenAI-compatible Chat Completions transports."""

import json
import os
from typing import Literal, Optional

from openai import APIError, APIStatusError, OpenAI

PROVIDERS = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY", "responses"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "chat"),
    "huggingface": ("https://router.huggingface.co/v1", "HF_TOKEN", "chat"),
    "compatible": (None, "CLARITY_API_KEY", "chat"),
}


class GenerationError(RuntimeError):
    """The model returned an unusable or incomplete response."""


class ProviderError(RuntimeError):
    """The provider could not complete the request."""


def unsupported_format(error):
    """Downgrade only when the endpoint explicitly rejects the output format."""
    text = str(error).lower()
    parameter = any(word in text for word in ("response_format", "json_schema", "json_object", "text.format"))
    unsupported = any(word in text for word in ("not supported", "does not support", "unsupported", "not available", "unknown parameter", "unrecognized"))
    return isinstance(error, APIStatusError) and error.status_code in (400, 422) and parameter and unsupported


class ModelBackend:
    """One configurable backend for OpenAI, OpenRouter, Hugging Face, and local servers."""

    def __init__(
        self,
        model: str,
        provider: str = "openai",
        base_url: Optional[str] = None,
        api: Optional[Literal["responses", "chat"]] = None,
        output_mode: str = "auto",
        api_key_env: Optional[str] = None,
        max_output_tokens: int = 8192,
        timeout: float = 120,
        max_retries: int = 2,
        client=None,
    ):
        if provider not in PROVIDERS:
            raise ValueError("Unknown provider. Choose openai, openrouter, huggingface, or compatible.")
        if not model or not model.strip():
            raise ValueError("A model ID is required.")
        if output_mode not in ("auto", "json_schema", "json_object", "prompt"):
            raise ValueError("Unknown output mode.")
        if max_output_tokens <= 0 or timeout <= 0 or max_retries < 0:
            raise ValueError("Token limit and timeout must be positive; retries must be non-negative.")
        default_url, default_key_env, default_api = PROVIDERS[provider]
        self.provider = provider
        self.model = model
        self.base_url = base_url or default_url
        if client is not None and base_url is None:
            self.base_url = str(client.base_url).rstrip("/")
        self.api = api or default_api
        self.output_mode = output_mode
        self.max_output_tokens = max_output_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self.output_modes = []
        self._resolved_mode = None
        self._client = client
        self._api_key_env = api_key_env or default_key_env
        if self.api not in ("responses", "chat"):
            raise ValueError("API must be responses or chat.")
        if not self.base_url:
            raise ValueError("The compatible provider requires --base-url.")

    def settings(self):
        """Stable run settings, excluding credentials."""
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api": self.api,
            "output_mode": self.output_mode,
            "max_output_tokens": self.max_output_tokens,
        }

    def _get_client(self):
        if self._client is None:
            key = os.environ.get(self._api_key_env)
            if not key:
                if self.provider != "compatible":
                    raise ProviderError("Set {} before running inference.".format(self._api_key_env))
                key = "local"
            self._client = OpenAI(
                api_key=key,
                base_url=self.base_url,
                timeout=self.timeout,
                max_retries=self.max_retries,
            )
        return self._client

    def generate(self, system, user, schema):
        """Return JSON. The pipeline validates schemas and cross-stage consistency."""
        modes = [self.output_mode]
        if self.output_mode == "auto":
            modes = ["json_schema", "json_object", "prompt"]
            if self._resolved_mode:
                modes = modes[modes.index(self._resolved_mode):]
        for position, mode in enumerate(modes):
            try:
                text = self._request(system, user, schema, mode)
            except APIError as error:
                if self.output_mode == "auto" and position < len(modes) - 1 and unsupported_format(error):
                    continue
                status = getattr(error, "status_code", None)
                suffix = " (HTTP {})".format(status) if status else ""
                raise ProviderError(
                    "{} request failed{}. Check credentials, model access, format support, and connection.".format(self.provider, suffix)
                ) from error
            self._resolved_mode = mode
            self.output_modes.append(mode)
            try:
                value = json.loads(text)
            except (json.JSONDecodeError, TypeError) as error:
                raise GenerationError("Model output was not a complete JSON object.") from error
            if not isinstance(value, dict):
                raise GenerationError("Model output must be a JSON object.")
            return value
        raise ProviderError("The provider rejected every output mode.")

    def _request(self, system, user, schema, mode):
        client = self._get_client()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        if mode != "json_schema":
            messages[0]["content"] += "\nReturn JSON matching this schema:\n" + json.dumps(schema)
        if self.api == "responses":
            text_format = {"type": mode}
            if mode == "json_schema":
                text_format = {"type": "json_schema", "name": "clarity_qa", "strict": True, "schema": schema}
            request = {
                "model": self.model,
                "input": messages,
                "max_output_tokens": self.max_output_tokens,
                "store": False,
            }
            if mode != "prompt":
                request["text"] = {"format": text_format}
            response = client.responses.create(**request)
            if response.status != "completed":
                raise GenerationError("The model response was incomplete. Increase --max-output-tokens or shorten the input.")
            for item in response.output:
                for content in getattr(item, "content", []):
                    if getattr(content, "type", None) == "refusal":
                        raise GenerationError("The model refused this exchange.")
            if not response.output_text:
                raise GenerationError("The model returned no text.")
            return response.output_text
        request = {"model": self.model, "messages": messages}
        token_parameter = "max_completion_tokens" if self.provider == "openai" else "max_tokens"
        request[token_parameter] = self.max_output_tokens
        if mode == "json_schema":
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "clarity_qa", "strict": True, "schema": schema},
            }
        elif mode == "json_object":
            request["response_format"] = {"type": "json_object"}
        response = client.chat.completions.create(**request)
        if not response.choices:
            raise GenerationError("The model returned no choices.")
        choice = response.choices[0]
        if choice.finish_reason != "stop":
            raise GenerationError("The model response did not finish normally. Increase the token budget if it was truncated.")
        if getattr(choice.message, "refusal", None):
            raise GenerationError("The model refused this exchange.")
        if not choice.message.content:
            raise GenerationError("The model returned no text.")
        return choice.message.content

    def close(self):
        if self._client is not None:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
