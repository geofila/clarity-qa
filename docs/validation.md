# Validation

Version 0.2.0, checked on 25 November 2026.

## Automated suite

All 52 tests passed in each of these environments:

| Environment | Python | OpenAI SDK | Pydantic | Filelock |
| --- | --- | --- | --- | --- |
| Local compatibility check | 3.9.6 | 2.30.0 | 2.12.5 | 3.19.1 |
| Isolated package check | 3.12.16 | 2.54.0 | 2.14.3 | 3.32.7 |

The suite covers the existing analysis and provider behavior, multilingual text preservation, fixed taxonomy definitions, source evidence, checkpoint resume and repair. Study tests cover whole-exchange overlap, the 3,000-unit workload example, unseen-source controls, duplicate and language checks, blinded imports, review requirements, reviewed reference corrections, incomplete/canceled annotations, and separate agreement and control metrics. Hand-calculated cases verify Fleiss' kappa and its undefined one-category case.

The end-to-end CLI test runs raw QA input through the real OpenAI SDK with a controlled HTTP transport, reviews generated control fixtures, exports individual Label Studio packs, and evaluates full annotation-export fixtures. Model responses are fixtures, not live inference results.

## Label Studio integration

The optional integration script uses the installed Label Studio 1.23.2 application, Django API views and a temporary SQLite database. It creates actual projects, imports files through the multipart import endpoint, submits synthetic annotations through the annotation endpoint, exports full JSON through the export endpoint, and evaluates those exports using this package.

The checked run produced:

| Check | Result |
| --- | --- |
| Projects created with the study XML | 3 |
| Tasks imported | 62 |
| Synthetic human-format annotations submitted | 62 |
| Canonical normal sub-question units recovered | 40 |
| Common sub-question units used for agreement | 8 |
| Additional control exposures | 6 |
| Evasion / clarity kappa for the unanimous fixture | 1.0 / 1.0 |
| Summary-error detection / reviewed-reference accuracy | 1.0 / 1.0 |

The fixture checks data flow and calculations, not annotation difficulty or model quality. The test does not use a browser, test frontend interactions, contact an existing deployment, or establish access isolation in Community Edition.

To reproduce the optional application integration, install Label Studio in a separate test environment. It is not a runtime dependency of this package. Using a separate environment also avoids coupling this package's OpenAI SDK requirement to Label Studio's own dependencies.

```bash
python3.12 -m venv /tmp/clarity-label-studio-test
/tmp/clarity-label-studio-test/bin/python -m pip install "label-studio==1.23.2"
PYTHONPATH=src:tests /tmp/clarity-label-studio-test/bin/python tests/integration_label_studio.py
```

The script disables analytics, Sentry and update checks for its temporary local application.

## Installation check

A built 0.2.0 wheel was installed with its declared dependencies in a clean Python 3.12 environment. The installed CLI exposes the study commands, and the study XML is included as package data. `pip check` reports no broken requirements. The automated tests also run against the installed package from outside the source checkout.

## Limits

No model-provider credential was configured, so live model generation was not checked. These tests do not establish that generated sub-questions, labels or counterfactual summaries are semantically correct in any language. The default control-review step requires a coordinator to verify the source label, misleading interpretation, language and suitability before distribution. A language-specific pilot is still needed to assess workload and annotation quality.
