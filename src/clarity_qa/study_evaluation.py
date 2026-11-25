"""Merge full Label Studio exports and score agreement separately from controls."""

import math
import re
from collections import Counter
from pathlib import Path

from .io import fingerprint
from .label_studio import ANNOTATION_FORMAT, ERROR_LABEL, utf16_slice
from .study import STUDY_SCHEMA, load_study, normal_data, read_json
from .taxonomy import ClarityLabel, EvasionLabel, TAXONOMY


def fleiss_kappa(rows, categories):
    """Fleiss' kappa for complete rows with a fixed number of independent raters."""
    n = len(rows[0]) if rows else 0
    result = {"items": len(rows), "annotators": n, "kappa": None,
              "observed_pairwise_agreement": None, "expected_agreement": None,
              "unanimous_fraction": None, "reason": None}
    if not rows:
        result["reason"] = "No complete shared items."
        return result
    if n < 2 or any(len(row) != n for row in rows):
        raise ValueError("Fleiss' kappa requires at least two ratings per complete row.")
    totals = Counter()
    observed = 0.0
    unanimous = 0
    for row in rows:
        if any(label not in categories for label in row):
            raise ValueError("Unknown category in agreement ratings.")
        counts = Counter(row)
        totals.update(counts)
        observed += sum(count * (count - 1) for count in counts.values()) / (n * (n - 1))
        unanimous += len(counts) == 1
    observed /= len(rows)
    expected = sum((totals[label] / (len(rows) * n)) ** 2 for label in categories)
    result.update(observed_pairwise_agreement=observed, expected_agreement=expected,
                  unanimous_fraction=unanimous / len(rows))
    if math.isclose(expected, 1.0, abs_tol=1e-12):
        result["reason"] = "Kappa is undefined because all ratings use one category."
    else:
        result["kappa"] = (observed - expected) / (1.0 - expected)
    return result


def parse_annotation(annotation):
    """Read legacy one-sub-question choice annotations for old distributions."""
    targets = {
        "evasion": ("sub_question", "choices"),
        "question_validity": ("sub_question", "choices"),
        "summary_faithfulness": ("sub_answer", "choices"),
        "corrected_summary": ("sub_answer", "textarea"),
        "corrections": ("sub_question", "textarea"),
    }
    values = {}
    results = annotation.get("result")
    if not isinstance(results, list):
        raise ValueError("An annotation must contain a result array.")
    for item in results:
        if not isinstance(item, dict):
            raise ValueError("Each annotation result must be an object.")
        name = item.get("from_name")
        if name not in targets or name in values:
            raise ValueError("Unknown or duplicate annotation control: {}.".format(name))
        target, kind = targets[name]
        if item.get("to_name") != target or item.get("type") != kind:
            raise ValueError("Annotation results do not match the study labeling configuration.")
        payload = item.get("value", {})
        if not isinstance(payload, dict):
            raise ValueError("Annotation value must be an object.")
        raw = payload.get("choices" if kind == "choices" else "text")
        if not isinstance(raw, list) or any(not isinstance(value, str) for value in raw):
            raise ValueError("Annotation values must be arrays of strings.")
        if kind == "choices":
            if len(raw) != 1:
                raise ValueError("Each study choice requires exactly one selected value.")
            values[name] = raw[0]
        else:
            values[name] = "\n".join(raw).strip()
    validity = values.get("question_validity")
    if validity not in ("Supported", "Unsupported"):
        raise ValueError("A completed annotation needs a valid question_validity choice.")
    if validity == "Supported":
        label = EvasionLabel(values.get("evasion"))
        summary = values.get("summary_faithfulness")
        if summary not in ("Supported", "Unsupported", "Uncertain"):
            raise ValueError("A supported sub-question needs a summary_faithfulness choice.")
        if summary == "Unsupported" and not values.get("corrected_summary"):
            raise ValueError("An unsupported summary needs corrected_summary text.")
        clarity = TAXONOMY[label][0].value
    else:
        # Ignore values retained by the UI after their conditional section is hidden.
        label, summary, clarity = None, None, None
    return {
        "question_validity": validity, "summary_faithfulness": summary,
        "evasion_label": label.value if label is not None else None, "clarity_label": clarity,
        "corrected_summary": values.get("corrected_summary", "") if summary == "Unsupported" else "",
        "corrections": values.get("corrections", ""),
    }


def parse_highlight_annotation(annotation, data, parts):
    """Align one complete paragraph highlight to each original sub-question."""
    results = annotation.get("result")
    if not isinstance(results, list):
        raise ValueError("An annotation must contain a result array.")
    text = data["sub_answers"]
    expected = {
        part["part_id"]: utf16_slice(text, part["start"], part["end"]).strip()
        for part in parts
    }
    parsed, region_ids = {}, set()
    corrections = None
    for item in results:
        if not isinstance(item, dict) or not isinstance(item.get("value"), dict):
            raise ValueError("Each annotation result needs an object value.")
        name, payload = item.get("from_name"), item["value"]
        if item.get("to_name") != "sub_answers":
            raise ValueError("Highlight results must target sub_answers.")
        if name == "corrections" and item.get("type") == "textarea":
            raw = payload.get("text")
            if corrections is not None or not isinstance(raw, list) or any(not isinstance(x, str) for x in raw):
                raise ValueError("Invalid or duplicate exchange corrections.")
            corrections = "\n".join(raw).strip()
            continue
        if name != "evasion" or item.get("type") != "labels":
            raise ValueError("Unknown highlight annotation control: {}.".format(name))
        if set(payload) - {"start", "end", "text", "labels"}:
            raise ValueError("Unexpected highlight payload fields.")
        labels = payload.get("labels")
        if not isinstance(labels, list) or len(labels) != 1 or labels[0] not in [x.value for x in EvasionLabel] + [ERROR_LABEL]:
            raise ValueError("Each answer highlight needs exactly one fixed label.")
        selected = utf16_slice(text, payload.get("start"), payload.get("end"))
        if "text" in payload and (not isinstance(payload["text"], str) or payload["text"].strip() != selected.strip()):
            raise ValueError("Highlight text does not match its source offsets.")
        matches = [part_id for part_id, paragraph in expected.items() if selected.strip() == paragraph]
        if len(matches) != 1:
            raise ValueError("Highlight exactly one entire numbered answer summary; partial or cross-answer spans cannot be aligned.")
        part_id = matches[0]
        # Text identity alone is insufficient if a malicious export changes offsets.
        span = next(p for p in parts if p["part_id"] == part_id)
        if not payload["start"] <= span["start"] < span["end"] <= payload["end"]:
            raise ValueError("Highlight offsets do not cover the expected answer summary.")
        region_id = item.get("id")
        if region_id is not None:
            if not isinstance(region_id, str) or not region_id:
                raise ValueError("Invalid highlight region ID.")
        if str(part_id) in parsed or (region_id is not None and region_id in region_ids):
            raise ValueError("Duplicate highlights for a numbered answer.")
        if region_id is not None:
            region_ids.add(region_id)
        label = None if labels[0] == ERROR_LABEL else EvasionLabel(labels[0])
        parsed[str(part_id)] = {
            "question_validity": "Unsupported" if label is None else "Supported",
            "evasion_label": label.value if label is not None else None,
            "clarity_label": TAXONOMY[label][0].value if label is not None else None,
            "summary_faithfulness": None, "corrected_summary": "", "corrections": "",
        }
    if len(parsed) != len(parts):
        raise ValueError("A completed exchange needs one highlight for every numbered answer summary.")
    for value in parsed.values():
        value["corrections"] = corrections or ""
    return {"parts": parsed, "corrections": corrections or ""}


def load_distribution(private, plan, distribution_id=None):
    if distribution_id is None:
        distribution_id = read_json(private / "latest_distribution.json")["distribution_id"]
    if not isinstance(distribution_id, str) or not re.fullmatch(r"[0-9a-f]{24}", distribution_id):
        raise ValueError("Invalid distribution ID.")
    manifest = read_json(private / "distributions" / (distribution_id + ".json"))
    without_id = {key: value for key, value in manifest.items() if key != "distribution_id"}
    if (manifest.get("schema_version") != STUDY_SCHEMA
            or manifest.get("distribution_id") != distribution_id
            or fingerprint(without_id)[:24] != distribution_id
            or manifest.get("plan_sha256") != fingerprint(plan)):
        raise ValueError("The distribution manifest does not match this study.")
    if manifest.get("annotation_format") not in (None, ANNOTATION_FORMAT):
        raise ValueError("Unsupported distribution annotation format.")
    return manifest


def read_annotator_exports(directory, manifest):
    directory = Path(directory)
    annotations = {}
    completion = {}
    for name in manifest["annotators"]:
        rows = read_json(directory / (name + ".json"))
        if not isinstance(rows, list):
            raise ValueError("Use full Label Studio JSON exports, saved under each annotator's filename.")
        expected = set(manifest["assignments"][name])
        seen, identities = set(), set()
        annotations[name] = {}
        for task in rows:
            if not isinstance(task, dict) or not isinstance(task.get("data"), dict):
                raise ValueError("Each exported task must include its original data object.")
            key = task["data"].get("task_key")
            if key not in expected or key in seen:
                raise ValueError("Unknown, incorrectly assigned or duplicate task in {}.json.".format(name))
            seen.add(key)
            if fingerprint(task["data"]) != manifest["tasks"][key]["data_sha256"]:
                raise ValueError("Exported task data changed after distribution; do not edit source tasks.")
            raw = task.get("annotations")
            if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
                raise ValueError("Use full JSON exports with an annotations array, not predictions or JSON_MIN.")
            active = [item for item in raw if item.get("was_cancelled") is not True]
            if len(active) > 1:
                raise ValueError("Each per-annotator export must have at most one completed annotation per task.")
            if not active:
                continue
            item = active[0]
            user = item.get("completed_by")
            if isinstance(user, dict):
                user = user.get("id", user.get("email"))
            if user is not None:
                identities.add(fingerprint(user))
            info = manifest["tasks"][key]
            annotations[name][key] = (
                parse_highlight_annotation(item, task["data"], info["parts"])
                if manifest.get("annotation_format") == ANNOTATION_FORMAT else parse_annotation(item)
            )
        if len(identities) > 1:
            raise ValueError("{}.json contains annotations from multiple user identities.".format(name))
        completion[name] = {
            "assigned": len(expected), "completed": len(annotations[name]),
            "missing_or_cancelled": len(expected) - len(annotations[name]),
            "exported_tasks": len(seen), "user_identity_present": bool(identities),
            "assigned_subquestions": sum(len(manifest["tasks"][key].get("parts", [None])) for key in expected),
            "completed_subquestions": sum(len(value.get("parts", [None])) for value in annotations[name].values()),
        }
    return annotations, completion


def counterfactual_metrics(name, annotations, manifest):
    controls = [key for key in manifest["assignments"][name] if key in manifest["controls"]]
    counts = {
        "assigned": len(controls), "completed": 0, "labeled": 0, "summary_checks": 0,
        "detected_summary_errors": 0, "counterlabel_selections": 0,
        "reviewed_labeled": 0, "correct_source_labels": 0,
    }
    for key in controls:
        annotation = annotations.get(key)
        if annotation is None:
            continue
        counts["completed"] += 1
        if manifest.get("annotation_format") == ANNOTATION_FORMAT:
            annotation = annotation["parts"][str(manifest["controls"][key]["part_id"])]
        if annotation["summary_faithfulness"] is not None:
            counts["summary_checks"] += 1
            counts["detected_summary_errors"] += annotation["summary_faithfulness"] == "Unsupported"
        if annotation["evasion_label"] is not None:
            counts["labeled"] += 1
            reference = manifest["controls"][key]
            counts["counterlabel_selections"] += annotation["evasion_label"] == reference["counter_label"]
            if reference["reference_label"] is not None:
                counts["reviewed_labeled"] += 1
                counts["correct_source_labels"] += annotation["evasion_label"] == reference["reference_label"]
    counts["summary_error_detection_rate"] = (
        counts["detected_summary_errors"] / counts["summary_checks"] if counts["summary_checks"] else None
    )
    counts["counterlabel_uptake_rate"] = (
        counts["counterlabel_selections"] / counts["labeled"] if counts["labeled"] else None
    )
    counts["reviewed_source_label_accuracy"] = (
        counts["correct_source_labels"] / counts["reviewed_labeled"] if counts["reviewed_labeled"] else None
    )
    return counts


def evaluate_study(directory, exports, distribution_id=None):
    private, plan, config = load_study(directory)
    manifest = load_distribution(private, plan, distribution_id)
    annotations, completion = read_annotator_exports(exports, manifest)
    ids = manifest["annotators"]
    assigned_sets = {name: set(manifest["assignments"][name]) for name in ids}
    shared = set(plan["shared_source_keys"])
    evasion_rows, clarity_rows = [], []
    common_expected, excluded_missing, excluded_invalid = 0, 0, 0
    normal_annotations = []
    for key, task in manifest["tasks"].items():
        if task["kind"] != "normal":
            continue
        assigned = [name for name in ids if key in assigned_sets[name]]
        source = plan["sources"][task["source_key"]]
        grouped = manifest.get("annotation_format") == ANNOTATION_FORMAT
        part_ids = [p["part_id"] for p in task["parts"]] if grouped else [task["part_id"]]
        for part_id in part_ids:
            collected = []
            for name in assigned:
                if key in annotations[name]:
                    value = annotations[name][key]
                    if grouped:
                        value = value["parts"][str(part_id)]
                    collected.append({"annotator_id": name, **value})
            data = normal_data(plan, task["source_key"], part_id)
            normal_annotations.append({
                "task_key": key, "unit_key": fingerprint([key, part_id])[:32],
                "source_key": task["source_key"], "source_id": source["id"],
                "part_id": part_id, "language": data["language"],
                "question": data["question"], "answer": data["answer"], "sub_question": data["sub_question"],
                "model_supporting_summary": data["sub_answer"], "annotations": collected,
            })
            if task["source_key"] not in shared:
                continue
            common_expected += 1
            if len(collected) != len(ids):
                excluded_missing += 1
            elif any(item["evasion_label"] is None for item in collected):
                excluded_invalid += 1
            else:
                evasion_rows.append([item["evasion_label"] for item in collected])
                clarity_rows.append([item["clarity_label"] for item in collected])
    warnings = []
    if any(value["review_status"] != "approved" for value in manifest["controls"].values()):
        warnings.append("Unreviewed pilot controls are present. Their model source labels are not scored as verified references.")
    if excluded_missing or excluded_invalid:
        warnings.append("Agreement uses only complete shared items with valid sub-questions; exclusions are reported separately.")
    return {
        "schema_version": STUDY_SCHEMA, "study_id": plan["study_id"],
        "annotation_format": manifest.get("annotation_format", "legacy-choices-v1"),
        "distribution_id": manifest["distribution_id"], "language": plan["language"],
        "completion": completion,
        "agreement": {
            "annotator_ids": ids,
            "shared_sources": len(shared), "expected_shared_subquestions": common_expected,
            "excluded_incomplete": excluded_missing, "excluded_invalid_subquestions": excluded_invalid,
            "evasion": fleiss_kappa(evasion_rows, [label.value for label in EvasionLabel]),
            "clarity": fleiss_kappa(clarity_rows, [label.value for label in ClarityLabel]),
        },
        "counterfactuals": {name: counterfactual_metrics(name, annotations[name], manifest) for name in ids},
        "metric_definitions": {
            "agreement": "Fleiss' kappa on completed, unchanged common sub-questions before adjudication. Controls are excluded.",
            "summary_error_detection_rate": "Unsupported summary choices divided by completed summary checks on controls; Uncertain is not detection.",
            "highlight_summary_checks": "The highlight-only interface has no separate summary-faithfulness choice. Summary check counts are zero and detection rates are null; free-text notes require manual review.",
            "exchange_corrections": "On grouped tasks, corrections are exchange-level notes repeated with each part for context. They are not automatically aligned, scored or treated as gold labels.",
            "counterlabel_uptake_rate": "Counterlabel choices divided by controls with a response-type label.",
            "reviewed_source_label_accuracy": "Correct source labels divided by labeled controls with a human-reviewed reference.",
            "identity": "Annotator identity follows the per-annotator export filename. User IDs from separate Label Studio instances are not globally comparable.",
        },
        "normal_annotations": normal_annotations,
        "warnings": warnings,
    }
