# Validation

Version 0.3.0, checked on 25 November 2026.

## Automated suite and installation

All **61 tests passed** on Python 3.9.6 and Python 3.12.16. A built 0.3.0 wheel was installed with its declared dependencies into a new Python 3.12 environment. All 61 tests also passed against that installed package from outside the source checkout. `pip check` reported no broken requirements. Both XML templates, the HTML instruction template and the PDF manual are present in the installed package.

The suite covers model/provider handling, multilingual text and evidence preservation, grouped source overlap, blinded exports, reviewed unseen-source controls, checkpoint resume, full JSON export parsing, variable part counts and explicit prediction opt-in. Highlight regressions cover UTF-16 offsets including emoji, missing/duplicate/partial/cross-answer spans, invalid labels and offsets, per-part Error decisions, exchange-level corrections, legacy choice distributions and hand-calculated Fleiss' kappa.

## Real Label Studio application integration

`tests/integration_label_studio.py` uses Label Studio 1.23.2, real Django project/import/annotation/export API views and temporary SQLite. Test annotations and model control responses are synthetic fixtures.

| Check | Result |
| --- | --- |
| Projects | 3 |
| Complete-exchange tasks imported and annotated | 34 |
| Highlight regions recovered | 68 |
| Ordinary sub-question units recovered | 40 |
| Common ordinary units used for agreement | 8 |
| Control exchange exposures | 6 |
| Evasion / clarity kappa for the unanimous fixture | 1.0 / 1.0 |
| Reviewed source-label accuracy on manipulated control parts | 1.0 |
| HTML instruction CSS retained after saving | Yes |

All parts of each exchange remain together. Each control changes exactly one summary. Controls and their unchanged companion parts are excluded from ordinary annotations and agreement. The highlight-only UI has no separate summary-faithfulness choice: automatic summary-error detection is `null`. Free-text error reports require coordinator review.

Reproduce the optional integration in an isolated environment; Label Studio is not a package runtime dependency:

```bash
python3.12 -m venv /tmp/clarity-label-studio-test
/tmp/clarity-label-studio-test/bin/python -m pip install "label-studio==1.23.2"
PYTHONPATH=src:tests /tmp/clarity-label-studio-test/bin/python tests/integration_label_studio.py
```

## Saved live English model run and final export

The [English toy inputs](../toy%20data/English/input.json) were processed with the OpenAI Responses API using `gpt-6.1-sol`. The original 0.2.0 model output and original run receipt are preserved. Model labels remain unreviewed. The saved output was re-exported using the standard 0.3.0 `prepare-study` and `export-study` commands without further model calls, producing **three complete-exchange tasks with nine numbered summaries**. It does not rely on a demo-only adapter.

The final JSON and XML were imported into a local Label Studio project and roundtripped with no annotations or predictions. Browser interaction then created three labels and an exchange-level note on one task, submitted them through the UI, and the standard evaluator recovered the three individual judgments from the actual browser-generated offsets. The [browser screenshot](images/label-studio-highlight-demo.jpg) shows the test fixture. These are software checks, not research annotations; the committed import is unannotated.

The instruction popup was also visually checked in the local application. The demo URL returned HTTP 200 at its Label Studio login page. This check did not alter the remote deployment or import files there.

## Annotator manual

The five-page PDF was updated for complete-exchange highlight annotation. Label examples still come from Table 4 of the original paper, with simplified or abridged wording. Updated workflow/checklist pages were rendered and visually inspected. The repository and packaged copies of the PDF are identical. Participant materials do not describe private control-generation details.

To rebuild the guide on macOS with Arial, or Linux with DejaVu Sans installed:

```bash
python -m pip install ".[docs]"
python scripts/build_annotator_guide.py
```

## Limits

Tests establish data flow, schema validation and agreement calculations. They do not establish that generated sub-questions, labels or counterfactual summaries are semantically correct in a new language. Counterfactual review requires a coordinator to verify each source label, misleading interpretation, language and suitability. Suggested new questions and free-text corrections require manual review and alignment. A language-specific pilot is needed to assess annotation quality and workload. Community Edition filenames and projects do not enforce per-user access isolation.
