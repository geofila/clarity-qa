"""Validated JSON inputs and resumable, atomic batch output."""

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .models import BatchResult, ItemError, QAPair
from .providers import GenerationError, ProviderError


def load_pairs(value, language=None):
    """Accept one object, an array, or an object with an items array."""
    if isinstance(value, dict) and "items" in value:
        if set(value) != {"items"}:
            raise ValueError("A batch wrapper must contain only the 'items' field.")
        value = value["items"]
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list) or not value:
        raise ValueError("Input must be a QA object, a non-empty array, or {'items': [...] }.")
    pairs = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError("Input item {} must be an object.".format(index))
        if language is not None:
            item = {**item, "language": language}
        try:
            pairs.append(QAPair.model_validate(item))
        except ValueError as error:
            raise ValueError("Invalid input item {}: {}".format(index, error)) from error
    return pairs


def fingerprint(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def atomic_json(path, value):
    """Publish a complete JSON file using a same-directory atomic replacement."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    handle, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_batch(pairs, pipeline, output, resume=False, progress=None):
    """Checkpoint each completed item. Callers must lock the output path."""
    output = Path(output)
    input_hash = fingerprint([pair.model_dump(mode="json") for pair in pairs])
    settings_hash = fingerprint(pipeline.settings())
    report = BatchResult(input_sha256=input_hash, settings_sha256=settings_hash)
    if resume and output.exists():
        report = BatchResult.model_validate_json(output.read_text(encoding="utf-8"))
        if report.input_sha256 != input_hash or report.settings_sha256 != settings_hash:
            raise ValueError("Resume requires the same inputs, order, language, model, endpoint, and pipeline settings.")
    completed = set()
    for result in report.results:
        index = result.input_index
        if index is None or index < 0 or index >= len(pairs) or index in completed:
            raise ValueError("The checkpoint contains invalid or duplicate input indexes.")
        pair = pairs[index]
        if (result.id, result.question, result.answer, result.metadata) != (pair.id, pair.question, pair.answer, pair.metadata):
            raise ValueError("Checkpoint results do not match the original inputs.")
        completed.add(index)
    for index, pair in enumerate(pairs):
        if index in completed:
            continue
        report.errors = [error for error in report.errors if error.input_index != index]
        try:
            result = pipeline.analyze(pair)
        except (GenerationError, ProviderError) as error:
            report.errors.append(ItemError(input_index=index, id=pair.id, message=str(error)))
            atomic_json(output, report.model_dump(mode="json"))
            raise
        result.input_index = index
        report.results.append(result)
        report.results.sort(key=lambda result: result.input_index)
        atomic_json(output, report.model_dump(mode="json"))
        if progress is not None:
            progress(index + 1, len(pairs))
    return report
