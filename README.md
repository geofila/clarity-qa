# CLARITY QA

Turn complete political question-answer exchanges into multilingual sub-questions, answer summaries, and response clarity predictions.


## Install and run

Requires Python 3.9 or newer and access to an instruction-following model.

```bash
git clone https://github.com/geofila/clarity-qa.git
cd clarity-qa
python -m venv .venv
source .venv/bin/activate
python -m pip install .

export OPENAI_API_KEY="your-api-key"
clarity-qa analyze \
  --provider openai --model gpt-4o-mini \
  --input examples/qa.en.json --output results.en.json
```

The model ID is configurable. Choose a model with good performance in the source language and enough context for the full exchange. Each exchange normally makes three model requests. Validation repairs and provider retries can add requests.

One exchange can also be passed directly.

```bash
clarity-qa analyze \
  --model gpt-4o-mini --language en \
  --question "When will the hospital open, and what will it cost?" \
  --answer "It will open in June. I do not know the final cost yet." \
  --output results.single.json
```

## JSON input

Use one object, an array of objects, or `{"items": [...]}`. `question` and `answer` are required non-empty strings. `id`, `language`, and `metadata` are optional. Put additional source fields inside `metadata`.

```json
{
  "id": "exchange-001",
  "language": "en",
  "question": "When will the new hospital open, and what will it cost?",
  "answer": "The hospital will open in June. I do not know the final cost yet.",
  "metadata": {"source_url": "https://example.org/interview"}
}
```

Omit `language` to let the model identify it. A per-record language is useful for mixed batches. `--language` overrides the language for every record. Titles, questions, and summaries follow the source language. JSON keys, labels, and taxonomy explanations stay in English. Evidence is copied from the original answer, including its original language.

```bash
clarity-qa analyze --model gpt-4o-mini \
  --input examples/batch.json --output results.batch.json

# Rerun the same command with --resume after an interruption.
clarity-qa analyze --model gpt-4o-mini \
  --input examples/batch.json --output results.batch.json --resume
```

Use `--input -` to read JSON from stdin. Completed exchanges are saved atomically after each item. A failed item is recorded in `errors`, and the command exits with status 2. Resume skips completed exchanges and retries unfinished exchanges. Input content, ordering, and inference settings must match the checkpoint. A file lock prevents simultaneous writers to the same output.

## Python API

```python
from clarity_qa import QAPipeline

with QAPipeline(provider="openai", model="gpt-4o-mini") as pipeline:
    result = pipeline.analyze(
        question="When will the hospital open, and what will it cost?",
        answer="It will open in June. I do not know the final cost yet.",
        language="en",
        id="hospital-1",
    )
    print(result.model_dump_json(indent=2))
```

`pipeline.analyze(qa_dict)` and `pipeline.analyze(QAPair(...))` accept the same input fields. Reuse an instance for a sequential batch. Use a separate instance for each concurrent worker.

## Providers

| Provider | Credential environment variable | Default API |
| --- | --- | --- |
| `openai` | `OPENAI_API_KEY` | Responses |
| `openrouter` | `OPENROUTER_API_KEY` | Chat Completions |
| `huggingface` | `HF_TOKEN` | Chat Completions |
| `compatible` | `CLARITY_API_KEY` if required by the server | Chat Completions |

The Python API accepts the same `provider`, `model`, `base_url`, `api`, and `output_mode` settings. Model IDs are provider-specific. Hugging Face uses its hosted inference router. A local Hugging Face model can be used through a running OpenAI-compatible server.

```bash
export OPENROUTER_API_KEY="your-api-key"
clarity-qa analyze --provider openrouter --model openai/gpt-4o-mini \
  --input examples/qa.en.json --output results.router.json

export HF_TOKEN="your-hf-token"
export HF_MODEL="your-supported-instruct-model-id"
clarity-qa analyze --provider huggingface --model "$HF_MODEL" \
  --input examples/qa.el.json --output results.hf.json

# Use the model ID exposed by your already-running local server.
clarity-qa analyze --provider compatible \
  --base-url http://localhost:8000/v1 --model your-served-model-id \
  --input examples/qa.en.json --output results.local.json
```

`--api-key-env VARIABLE_NAME` selects another credential environment variable. Credentials are read at runtime and excluded from output. For authenticated compatible endpoints, set `CLARITY_API_KEY`. A compatible server that needs no authentication receives a dummy `local` key.

Structured JSON is requested first. In `auto` mode, an explicit unsupported-format error triggers a fallback to JSON-object mode and then prompt-based JSON. Every mode uses the same local schema and consistency checks. Select `--output-mode json_schema` to require schema-constrained generation. Use `--api chat` for Chat Completions on OpenAI, or `--api responses` for a compatible Responses endpoint.

Adjust `--max-output-tokens`, `--timeout`, `--max-retries`, and `--repair-attempts` when needed. The default is one repair attempt for invalid output per stage. Truncated, refused, malformed, or inconsistent responses produce an error after the allowed attempts.

## Output

The CLI writes a JSON object containing `results`, `errors`, input and settings fingerprints, and a schema version. Each result preserves the original question, answer, ID, and metadata. It adds question and answer summaries, a detected or supplied language, sub-questions, and provenance. No synthetic original text is substituted for the source exchange.

Each sub-question has the following fields.

| Field | Meaning |
| --- | --- |
| `id` | Consecutive part number, starting at 1 |
| `title` | Short title in the source language |
| `question` | Self-contained sub-question in the source language |
| `answer_summary` | Source-language summary of the response to this part, including an explicit indication when it is unanswered |
| `answer_evidence` | Exact original-answer quotations with Python Unicode character offsets, using an exclusive `end` |
| `evasion_label` | One of the nine fixed English labels |
| `clarity_label` | The corresponding fixed clarity category |
| `label_explanation` | Fixed English definition of the evasion label |
| `clarity_explanation` | Fixed English definition of the clarity category |

The per-part `answer_summary` is the sub-answer representation. Evidence quotes supply the corresponding original text. If a quote occurs more than once, its first occurrence supplies the offsets. If the input question contains only statements, `sub_questions` is empty and no labels are invented.

Explanations are canonical taxonomy definitions inserted by code. Instance-specific generated reasoning is excluded from these fields. Every prediction is marked `review_status: "unreviewed"` in provenance. Model quality and multilingual decomposition accuracy require human evaluation.

`examples/output.el.json` is an illustrative Greek output assembled from test fixtures. It shows the full format and is explicitly marked with `provider: "fixture"`.

## Fixed taxonomy

| Evasion label | Clarity label |
| --- | --- |
| Explicit | Clear Reply |
| Implicit | Ambivalent |
| Dodging | Ambivalent |
| Deflection | Ambivalent |
| Partial/half-answer | Ambivalent |
| General | Ambivalent |
| Declining to answer | Clear Non-Reply |
| Claims ignorance | Clear Non-Reply |
| Clarification | Clear Non-Reply |

`clarity-qa taxonomy` prints all fixed definitions as JSON. The strings follow the released QEvasion dataset, including `Partial/half-answer` and `Ambivalent`. Figures 3 and 20 use some abbreviated display names. Earlier helper scripts in the upstream repository contain an older taxonomy. This package uses the final nine-label taxonomy.

## Annotation studies and counterfactuals

Use `prepare-study` to assign whole source exchanges to an annotation team, with a common sample and optional counterfactual quality checks. The default example shares 20% of original QA exchanges with all three annotators, distributes the rest once, and adds 30 controls per person. Adjust the example config for your dataset size and team.

```bash
export CLARITY_MODEL="your-supported-model-id"

clarity-qa prepare-study \
  --input my-qa.json --config examples/study.config.json \
  --output study-es --provider openai --model "$CLARITY_MODEL"

# Review the candidates and verified source labels in:
# study-es/private/counterfactuals.json

clarity-qa export-study --study study-es --output label-studio-es

# Import one JSON file into each annotator's Label Studio project.
# Later, save full project JSON exports in exports-es/ under the annotator filenames.
clarity-qa evaluate-study \
  --study study-es --exports exports-es --output evaluation.es.json
```

Raw QA input runs through the existing analysis pipeline. Completed analysis output can also be supplied. Use `examples/study.no-controls.config.json` to disable controls; processed input without controls needs no model or API key. Add `--resume` to preparation after an interruption.

Counterfactual tasks retain the original source and sub-question, replacing one supporting answer summary with deliberately unfaithful text. Controls are additional tasks, hidden among ordinary tasks, and come from sources the recipient does not otherwise see. The default review gate blocks export until a coordinator checks the variants and supplies verified reference labels. Original model predictions, source metadata and control answers are absent from the annotators' imports.

Agreement uses only complete ordinary shared items, before adjudication. Controls have separate detection, counterlabel-uptake and reviewed-reference metrics and are excluded from `normal_annotations`. With 20% overlap, only that common subset receives judgments from everyone.

**Import only the public pack, never the study's `private/` directory.** Community Edition projects do not enforce per-user task assignment or access isolation. Use separate projects with independent work instructions, separate instances, or Enterprise permissions as appropriate.

See the [annotation-study guide](docs/annotation-studies.md) for configuration, review, import, evaluation, denominator definitions and the paper alignment.

For annotators, share the [CLARITY Annotator Guide (PDF)](docs/CLARITY-Annotator-Guide.pdf). It covers the labeling workflow, the nine response types, examples adapted from Table 4 of the paper, and a submission checklist.

## Label Studio prediction review

```bash
clarity-qa export-label-studio \
  --input results.batch.json \
  --output label-studio-tasks.json \
  --config label-studio-config.xml
```

Paste the XML into the project's labeling interface and import the tasks JSON. There is one task per sub-question, with the original exchange, summaries, evidence, and predicted label. Predictions appear as pre-annotations. The interface also lets annotators flag unsupported sub-questions and record missing questions or corrections, following Figures 19 and 20. Only completed exchanges are exported.

## Paper alignment and references

The pipeline adapts the three main preprocessing prompts in Appendix H. It identifies the requested information, summarizes the answer for each part, and classifies the response using the original exchange. Table 4 supplies the taxonomy semantics. Figures 19 and 20 supply the annotation fields and review controls. The multilingual prompts, structured JSON contracts, evidence offsets, and provider adapters are additions in this package.

- [Original paper](https://aclanthology.org/2024.findings-emnlp.300/)
- [Original code](https://github.com/konstantinosftw/Question-Evasion)
- [Released QEvasion dataset](https://huggingface.co/datasets/ailsntua/QEvasion)
- [Official OpenAI documentation for structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
- [Hugging Face Chat Completion](https://huggingface.co/docs/inference-providers/en/tasks/chat-completion)

## Verification

```bash
python -m unittest discover -s tests -v
```

Tests use deterministic fixtures and the real OpenAI SDK with mocked HTTP responses. They cover multilingual field preservation, the taxonomy, evidence checking, repair behavior, provider fallback, checkpoint resume, grouped overlap, unseen-source controls, coordinator review, blinded exports, full-export merging and hand-calculated agreement cases. Live model inference has not been verified in this environment because no provider credential was configured. Counterfactual semantic suitability and multilingual quality require coordinator review and a local pilot. See [validation notes](docs/validation.md) for the checked environments and Label Studio integration results.
