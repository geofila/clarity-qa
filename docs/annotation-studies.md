# Annotation studies

Prepare independent annotator files with a common sample, optional counterfactual quality checks, and a Label Studio interface. After annotation, merge full JSON exports and calculate agreement separately from control performance.

## Prepare the study

Use a raw QA object, a raw array or `{"items": [...]}`, or completed output from `clarity-qa analyze`. Raw exchanges run through the existing decomposition, summary and classification pipeline. Processed input reuses those results. A batch with recorded errors must be completed first.

Create a separate study for each language and team. Each source needs at least one sub-question. Duplicate exchanges, duplicate supplied IDs and mixed languages are rejected. Original source text and evidence remain unchanged.

```bash
export CLARITY_MODEL="your-supported-model-id"

clarity-qa prepare-study \
  --input my-qa.json \
  --config examples/study.config.json \
  --output study-es \
  --provider openai --model "$CLARITY_MODEL"
```

The same provider, endpoint, format and retry options as `analyze` are available. Provider credentials remain in environment variables. This command prepares coordinator files. It does not upload data to Label Studio.

Preparation checkpoints each completed exchange and counterfactual candidate. Repeat the same command with `--resume` after an interruption. Inputs, ordering, configuration and model settings must match. Completed requests and existing reviews are reused. The seed controls assignment, sampling and task ordering. Cached model output preserves an existing run; a seed does not guarantee identical output from a remote model on a new run.

For processed input without controls, no provider, model or API key is needed:

```bash
clarity-qa prepare-study \
  --input results.es.json \
  --config examples/study.no-controls.config.json \
  --output study-es
```

## Configuration

```json
{
  "annotators": 3,
  "seed": 42,
  "overlap": {
    "fraction": 0.20,
    "shared_with": "all",
    "unit": "source_qa"
  },
  "counterfactuals": {
    "enabled": true,
    "count_per_annotator": 30,
    "require_review": true,
    "min_length_ratio": 0.5,
    "max_length_ratio": 2.0
  },
  "show_model_labels": false
}
```

`annotators` accepts a count or unique IDs such as `["alice", "bob", "carol"]`. IDs use letters, digits, underscores and hyphens and determine filenames. Unknown configuration keys are rejected. Study mode requires `show_model_labels: false`; use the separate `export-label-studio` command for reviewing visible model predictions.

`fraction` means the fraction of unique original QA exchanges shared with **all** annotators. All sub-questions from a source stay together. Other sources go to one annotator, balancing the number of sub-questions. The common sources and their summaries are identical across annotator files. Each file has its own seeded task order.

The number of common sources is rounded to the nearest integer, with halves rounded up. If a positive fraction selects zero sources, preparation fails with a request to increase the fraction or dataset size. Shared fractions among final sub-question units can differ from the source fraction when exchanges have different numbers of parts.

For 3,000 sources containing one sub-question each, three annotators and 20% overlap, each annotator receives 600 common tasks and 800 exclusive normal tasks. With 30 controls per annotator, each receives 1,430 tasks. Only the common 20% has judgments from everyone. This configuration does not collect three judgments for every normal task.

`count_per_annotator` counts additional control tasks per person. Three annotators and 30 controls means 90 generated variants. Each control changes one supporting sub-answer summary and keeps the original question, answer and sub-question. Its source comes from a normal group assigned to another annotator. No annotator sees both versions, or two control tasks from the same source. Common sources cannot supply controls. If too few unseen sources remain, preparation fails instead of repeating sources or silently reducing the count. With 100% overlap, unseen-source controls require a separate future design; the current tool rejects that combination.

Length ratios compare trimmed character counts to the faithful supporting summary. They are mechanical checks, not verification of meaning or language quality. Generated text follows the source language. Labels, definitions, configuration keys and instructions stay in English.

## Review counterfactual candidates

Only the coordinator should access the study directory:

```text
study-es/
  private/
    state.json
    plan.json
    analysis-checkpoint.json       # Raw input only
    counterfactuals.json
    REVIEW.md
```

`counterfactuals.json` contains the original exchange, target sub-question, faithful and misleading summaries, the model's proposed source label, the counterlabel, assignment information and a review object. The model's source label is not verified ground truth.

Check that the sub-question is valid, the replacement summary is unfaithful to the original answer, it plausibly supports the counterlabel, and its language and length are suitable. Correct `misleading_summary` if necessary. Change only the review object and, if needed, the misleading summary and counterlabel. The source and assignment fields are checked against the frozen plan. The initial counterlabel remains recorded in the plan as generation provenance.

For an approved candidate, supply a named reviewer and the independently verified label for the **original response**:

```json
"review": {
  "status": "approved",
  "reviewer": "Reviewer name",
  "reference_label": "Explicit",
  "notes": "Checked the source and replacement summary."
}
```

`Explicit` is illustrative. Choose the correct label after reviewing that exchange. It must differ from the counterlabel. If the initially selected counterlabel turns out to be the correct source label, choose a different counterlabel and rewrite the misleading summary to support it. The counterlabel must still differ from the model's initial source prediction. Record changes in review notes. A rejected candidate blocks export. Replace its misleading summary and review it again, or prepare a new study with a different configuration. The tool never fills missing controls with unreviewed duplicates.

With `require_review: true`, all requested controls must be present and approved before export. Setting it to `false` allows pending candidates in an exploratory pilot, but their proposed model labels are never scored as verified references. Rejected candidates always block export. Normal summaries also need human checking during annotation.

## Export and import

```bash
clarity-qa export-study --study study-es --output label-studio-es
```

This writes a new public-only directory:

```text
label-studio-es/
  annotator_01.json
  annotator_02.json
  annotator_03.json
  labeling_config.xml
  annotation_instructions.html
  CLARITY-Annotator-Guide.pdf
  IMPORT.md
```

Create one project per annotator. Paste `labeling_config.xml` into Labeling Setup and import only that person's JSON file. Paste `annotation_instructions.html` into the project Instructions field and share the included PDF guide. Each task contains a complete original exchange, all numbered sub-questions and their numbered answer summaries. Select a response label and highlight each entire numbered answer summary. Use the Error label for a proposed question absent from the original. Record missing questions as `Question: Label` and report inaccurate summaries in the corrections field. Model labels are never preselected in study tasks.

Public tasks contain an opaque `task_key`, source-language text and fixed taxonomy definitions. They omit model predictions, source metadata, reference labels, counterlabels, control flags and model-selected evidence snippets. In particular, a faithful global summary is not shown alongside a manipulated part summary. Both normal and control tasks use the same fields and interface.

**Never upload `private/`, the candidates file or the coordinator manifests.** A field hidden by XML is not a secure place to keep a reference label.

In Label Studio Community Edition, per-annotator filenames and projects do not enforce assignment or access isolation. All users can see all projects. Ask participants to work only in their own project and independently. Use separate instances or Enterprise permissions when independent access must be enforced. Automatic per-user assignment is not performed by this file exporter.

An immutable distribution manifest is saved under `study-es/private/distributions/`. The distribution ID is printed by the command and included in `IMPORT.md`. Preserve this manifest with the annotation campaign. A later export to a new directory can produce a different distribution after reviews change. Existing output directories are never overwritten. The frozen allocation plan cannot be edited; changing source data or configuration requires a new study.

## Evaluate full Label Studio exports

Export each project using **JSON**, not JSON_MIN. Save each file under the corresponding annotator ID:

```text
exports-es/
  annotator_01.json
  annotator_02.json
  annotator_03.json
```

```bash
clarity-qa evaluate-study \
  --study study-es \
  --exports exports-es \
  --output evaluation.es.json
```

The latest distribution is used by default. Pass `--distribution DISTRIBUTION_ID` when evaluating an earlier distribution. Task keys and source-data fingerprints must match the selected distribution. Predictions do not count as annotations. Canceled tasks are incomplete. Each per-annotator file may contain at most one completed human annotation per task and one observed user identity. Identity follows the assigned filename; numeric user IDs from separate instances are not globally comparable.

New distributions use `exchange-spans-v1`. Existing frozen plans remain readable, and earlier per-sub-question choice distributions can still be evaluated with their original distribution ID and task files. Exporting a plan with version 0.3.0 creates the new grouped layout. Do not combine task files, XML or annotations from different distributions.

The report contains:

- Completion counts in exchange tasks and sub-question units, plus missing or canceled tasks per annotator.
- Fleiss' kappa for the nine response labels and the three derived clarity categories. Only common, unchanged tasks with complete valid ratings enter agreement. Counts of excluded incomplete or unsupported sub-questions are reported. One-category data produces an undefined kappa (`null`), rather than a misleading perfect score.
- Counterlabel uptake and reviewed-reference accuracy on the manipulated part of each control exchange. Other parts of control exchanges are excluded from normal annotations and agreement. The highlight-only interface has no separate summary-faithfulness choice, so automatic summary-error detection is `null`, not inferred from free-text comments. Legacy choice-based distributions retain their separate summary-check metrics. All numerators and denominators are included. A missing response-type label does not enter label-accuracy denominators; completion and check counts show that omission separately.
- `normal_annotations`, containing original exchanges and all collected ordinary human judgments and corrections. Controls are excluded. Supporting model summaries remain explicitly named as model output. The tool does not turn majority votes, model predictions or free-text corrections into adjudicated gold data automatically.

Calculate agreement before discussing or adjudicating disagreements. Each imported task is one full exchange; exported highlights are aligned to individual generated parts using private UTF-16 paragraph offsets. The evaluator requires one complete paragraph highlight per part and rejects missing, duplicate, partial, cross-answer or invalid highlights. Newly suggested questions in the corrections field need coordinator review and alignment before they become additional dataset units.

## Relation to the paper

Appendix A.3 of [I Never Said That](https://aclanthology.org/2024.findings-emnlp.300.pdf) describes 31 additional counterfactual sQA samples. Appendix H provides the misleading-summary prompt. Original QA text remained available, counterlabels were hidden, and the researchers manually checked the generated controls. Section 4 describes 317 common QA pairs annotated by all three annotators.

This package adapts that approach with configurable per-annotator control counts, source-group overlap, blinded individual exports and explicit review records. It replaces exactly one supporting sub-answer summary inside a complete-exchange control task, preserving the source, all sub-questions and the other summaries. It does not reproduce the original campaign's exact sample allocation or claim the paper's attention-check results for new data.

Label Studio references: [import tasks](https://labelstud.io/guide/tasks), [pre-annotations](https://labelstud.io/guide/predictions), [Community Edition users](https://labelstud.io/guide/signup), [edition differences](https://labelstud.io/guide/enterprise_features), [conditional View](https://labelstud.io/tags/view).

## Release 0.3.0 example

The [English toy demo](../toy%20data/English/README.md) contains actual saved OpenAI output and a ready-to-import three-exchange annotation pack. The [platform demo](http://spock.deep.islab.ntua.gr:9097) is available for exploring the interface.
