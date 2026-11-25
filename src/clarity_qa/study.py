"""Reproducible assignment, reviewed controls, and blinded Label Studio packs."""

import json
import math
import random
import shutil
import tempfile
from importlib.resources import files
from pathlib import Path

from filelock import FileLock

from . import __version__
from .counterfactuals import COUNTERFACTUAL_PROMPT_VERSION, generate_counterfactual, validate_summary
from .io import atomic_json, fingerprint, load_pairs, run_batch
from .models import Analysis, BatchResult
from .label_studio import ANNOTATION_FORMAT, annotation_instructions, exchange_data, labeling_config
from .study_models import CounterfactualCandidate, StudyConfig
from .taxonomy import TAXONOMY, EvasionLabel

STUDY_SCHEMA = "1.0"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def processed_input(value):
    """Return validated analyses, or None for a raw QA input."""
    if isinstance(value, dict) and "results" in value:
        report = BatchResult.model_validate(value)
        if report.errors:
            raise ValueError("The analysis batch contains errors. Complete it before preparing a study.")
        analyses = report.results
    else:
        items = value if isinstance(value, list) else [value]
        markers = [isinstance(item, dict) and "sub_questions" in item for item in items]
        if not any(markers):
            return None
        if not all(markers):
            raise ValueError("Do not mix raw QA objects and processed analyses in one input.")
        analyses = [Analysis.model_validate(item) for item in items]
    if not analyses:
        raise ValueError("A study needs at least one completed analysis.")
    return analyses


def validate_sources(analyses):
    """Reject duplicates, empty groups and mixed languages before assignment."""
    languages, content_keys, identities = set(), set(), set()
    sources = {}
    for analysis in analyses:
        languages.add(analysis.language.casefold())
        if not analysis.sub_questions or analysis.language.casefold() == "auto":
            raise ValueError("Every source must have sub-questions and an identified language.")
        texts = [analysis.question, analysis.answer]
        for part in analysis.sub_questions:
            texts.extend([part.question, part.title, part.answer_summary])
        if any(not text.strip() for text in texts):
            raise ValueError("Sources, sub-questions and summaries must contain text.")
        content_key = fingerprint({
            "question": " ".join(analysis.question.split()),
            "answer": " ".join(analysis.answer.split()),
            "language": analysis.language.casefold(),
        })
        if content_key in content_keys:
            raise ValueError("Duplicate source exchanges would contaminate assignment and overlap.")
        content_keys.add(content_key)
        if analysis.id is not None:
            identity = fingerprint({"id": analysis.id})
            if identity in identities:
                raise ValueError("Source IDs must be unique when supplied.")
            identities.add(identity)
        key = "source_" + content_key[:24]
        sources[key] = analysis.model_dump(mode="json")
    if len(languages) != 1:
        raise ValueError("Prepare a separate study for each language and its annotation team.")
    return sources


def task_key(study_id, source_key, part_id, variant="normal"):
    return fingerprint([study_id, source_key, part_id, variant])[:32]


def allocation_sizes(source_count, config):
    shared = int(math.floor(source_count * config.overlap.fraction + 0.5))
    if config.overlap.fraction and not shared:
        raise ValueError("The requested overlap selects zero sources. Increase the fraction or dataset size.")
    private_count = source_count - shared
    controls = config.counterfactuals.count_per_annotator if config.counterfactuals.enabled else 0
    # At least one person owns ceil(private_count / annotators) normal groups.
    available = private_count - math.ceil(private_count / len(config.annotator_ids()))
    if controls > available:
        raise ValueError(
            "Requested {} controls per annotator, but at most {} unseen source exchanges "
            "can be available to everyone. Reduce controls or overlap, or provide more sources.".format(controls, available)
        )
    return shared, controls, private_count - controls


def make_plan(analyses, config, study_id):
    """Assign whole source groups, then sample controls from unseen groups."""
    sources = validate_sources(analyses)
    ids = config.annotator_ids()
    rng = random.Random(config.seed)
    keys = sorted(sources)
    shuffled = list(keys)
    rng.shuffle(shuffled)
    count, cf_count, ownership_limit = allocation_sizes(len(keys), config)
    shared = sorted(shuffled[:count])
    remainder = shuffled[count:]
    # Largest groups first. Seeded tie ordering avoids a fixed annotator bias.
    remainder.sort(key=lambda key: len(sources[key]["sub_questions"]), reverse=True)
    assignments = {name: list(shared) for name in ids}
    owned_counts = {name: 0 for name in ids}
    workloads = {name: sum(len(sources[key]["sub_questions"]) for key in shared) for name in ids}
    for key in remainder:
        available_owners = [name for name in ids if owned_counts[name] < ownership_limit]
        minimum = min(workloads[name] for name in available_owners)
        owner = rng.choice([name for name in available_owners if workloads[name] == minimum])
        assignments[owner].append(key)
        owned_counts[owner] += 1
        workloads[owner] += len(sources[key]["sub_questions"])
    slots = []
    for name in ids:
        eligible = [key for key in remainder if key not in assignments[name]]
        if len(eligible) < cf_count:
            raise ValueError(
                "{} needs {} unseen source exchanges for controls, but only {} are available. "
                "Reduce count_per_annotator or overlap, or provide more sources.".format(name, cf_count, len(eligible))
            )
        for key in rng.sample(eligible, cf_count):
            part = rng.choice(sources[key]["sub_questions"])
            labels = [label.value for label in EvasionLabel if label.value != part["evasion_label"]]
            counter_label = rng.choice(labels)
            candidate_id = fingerprint([study_id, name, key, part["id"], "control"])[:32]
            slots.append({
                "candidate_id": candidate_id, "source_key": key, "part_id": part["id"],
                "assigned_annotator": name, "counter_label": counter_label,
            })
    return {
        "schema_version": STUDY_SCHEMA, "package_version": __version__, "study_id": study_id,
        "config": config.model_dump(mode="json"), "language": analyses[0].language,
        "sources": sources, "shared_source_keys": shared, "assignments": assignments,
        "counterfactual_slots": slots,
    }


def _candidate_context(plan, slot):
    analysis = Analysis.model_validate(plan["sources"][slot["source_key"]])
    part = next(part for part in analysis.sub_questions if part.id == slot["part_id"])
    return analysis, part


def _validate_candidate(candidate, plan, slot, config):
    analysis, part = _candidate_context(plan, slot)
    expected = {
        **{key: value for key, value in slot.items() if key != "counter_label"},
        "question": analysis.question, "answer": analysis.answer,
        "sub_question": part.question, "faithful_summary": part.answer_summary,
        "proposed_source_label": part.evasion_label.value,
    }
    actual = candidate.model_dump(mode="json")
    if any(actual[key] != value for key, value in expected.items()):
        raise ValueError("Counterfactual source, assignment or source-label metadata does not match the study plan.")
    validate_summary(candidate.misleading_summary, candidate.faithful_summary, config.counterfactuals)


def load_study(directory):
    private = Path(directory) / "private"
    state = read_json(private / "state.json")
    plan = read_json(private / "plan.json")
    if state.get("schema_version") != STUDY_SCHEMA or plan.get("schema_version") != STUDY_SCHEMA:
        raise ValueError("Unsupported study schema version.")
    if fingerprint(plan) != state.get("plan_sha256"):
        raise ValueError("The frozen study plan has changed. Prepare a new study instead.")
    config = StudyConfig.model_validate(plan["config"])
    return private, plan, config


def load_candidates(private, plan, config):
    rows = read_json(private / "counterfactuals.json")
    if not isinstance(rows, list):
        raise ValueError("counterfactuals.json must contain a JSON array.")
    candidates = {}
    slots = {slot["candidate_id"]: slot for slot in plan["counterfactual_slots"]}
    for row in rows:
        candidate = CounterfactualCandidate.model_validate(row)
        if candidate.candidate_id in candidates or candidate.candidate_id not in slots:
            raise ValueError("Duplicate or unknown counterfactual candidate ID.")
        _validate_candidate(candidate, plan, slots[candidate.candidate_id], config)
        candidates[candidate.candidate_id] = candidate
    return candidates


def prepare_study(value, config, directory, pipeline=None, resume=False):
    """Checkpoint analysis and control generation; never distribute pending controls."""
    config = StudyConfig.model_validate(config)
    analyses = processed_input(value)
    pairs = None if analyses is not None else load_pairs(value)
    if pairs is not None:
        declared = {pair.language.casefold() for pair in pairs if pair.language.casefold() != "auto"}
        if len(declared) > 1:
            raise ValueError("Prepare a separate study for each language.")
        allocation_sizes(len(pairs), config)
    needs_model = analyses is None or (config.counterfactuals.enabled and config.counterfactuals.count_per_annotator > 0)
    if needs_model and pipeline is None:
        raise ValueError("Raw input and counterfactual generation require a configured model pipeline.")
    settings = pipeline.settings() if needs_model else None
    identity = {
        "input_sha256": fingerprint(value), "config": config.model_dump(mode="json"),
        "generation_settings": settings, "counterfactual_prompt_version": COUNTERFACTUAL_PROMPT_VERSION,
        "schema_version": STUDY_SCHEMA,
    }
    directory = Path(directory)
    directory.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(directory.resolve()) + ".lock", timeout=0):
        state_path = directory / "private/state.json"
        if directory.exists() and (not resume or not state_path.is_file()):
            raise ValueError("Study output already exists. Use --resume with the same settings, or a new directory.")
        if state_path.exists():
            state = read_json(state_path)
            if state.get("identity") != identity:
                raise ValueError("Resume requires identical input, config, model and generation settings.")
        else:
            state = {"schema_version": STUDY_SCHEMA, "identity": identity, "plan_sha256": None}
            atomic_json(state_path, state)
        private = state_path.parent
        if analyses is None:
            report = run_batch(pairs, pipeline, private / "analysis-checkpoint.json", resume=resume)
            analyses = report.results
        plan = make_plan(analyses, config, fingerprint(identity)[:24])
        if state["plan_sha256"] is None:
            atomic_json(private / "plan.json", plan)
            state["plan_sha256"] = fingerprint(plan)
            atomic_json(state_path, state)
        else:
            if fingerprint(plan) != state["plan_sha256"]:
                raise ValueError("Cached study assignments do not match the current sources.")
            load_study(directory)
        if not (private / "counterfactuals.json").exists():
            atomic_json(private / "counterfactuals.json", [])
        candidates = load_candidates(private, plan, config)
        for slot in plan["counterfactual_slots"]:
            if slot["candidate_id"] in candidates:
                continue
            analysis, part = _candidate_context(plan, slot)
            summary = generate_counterfactual(pipeline, analysis, part, slot["counter_label"], config.counterfactuals)
            candidate = CounterfactualCandidate(
                **slot, question=analysis.question, answer=analysis.answer,
                sub_question=part.question, faithful_summary=part.answer_summary,
                proposed_source_label=part.evasion_label, misleading_summary=summary,
                generation_settings={**settings, "counterfactual_prompt_version": COUNTERFACTUAL_PROMPT_VERSION},
            )
            candidates[candidate.candidate_id] = candidate
            atomic_json(private / "counterfactuals.json", [item.model_dump(mode="json") for item in candidates.values()])
        instructions = (
            "# Coordinator review\n\n"
            "Keep this private directory outside Label Studio and away from annotators.\n\n"
            "Review each entry in counterfactuals.json against the original question and answer. "
            "Confirm that the sub-question is valid and the edited supporting summary is unfaithful, "
            "plausibly supports counter_label, and stays in the source language. "
            "The proposed_source_label is a model prediction, not ground truth.\n\n"
            "To approve, set review.status to approved, review.reviewer to your name and "
            "review.reference_label to the verified label for the ORIGINAL response. "
            "It must differ from counter_label. Edit misleading_summary if necessary. "
            "Do not edit source or assignment fields. Rejected or missing controls block export. "
            "Replace a rejected misleading_summary and review it again. If the initial counter_label "
            "is actually the correct source label, choose a different counter_label and rewrite its "
            "misleading_summary before approval. Record the change in review.notes.\n\n"
            "When require_review is false, pending candidates may be exported for a pilot. "
            "Their source-label accuracy will not be scored as verified ground truth. "
            "Rejected candidates are never exported.\n\n"
            "Run export-study after review. Import only its public files, never this directory.\n"
        )
        (private / "REVIEW.md").write_text(instructions, encoding="utf-8")
    return plan


def normal_data(plan, source_key, part_id):
    analysis, part = _candidate_context(plan, {"source_key": source_key, "part_id": part_id})
    return {
        "task_key": task_key(plan["study_id"], source_key, part_id),
        "language": analysis.language, "question": analysis.question, "answer": analysis.answer,
        "sub_question": part.question, "sub_answer": part.answer_summary,
        "taxonomy_definitions": "\n\n".join(
            "{} - {}".format(label.value, definition) for label, (_, definition) in TAXONOMY.items()
        ),
    }


def export_study(directory, output):
    """Write a new, public-only pack and an immutable private distribution manifest."""
    directory, output = Path(directory), Path(output)
    private, plan, config = load_study(directory)
    # Public exports may not contain, replace, or be nested inside coordinator files.
    if output.resolve() == directory.resolve() or output.resolve().is_relative_to(private.resolve()) or directory.resolve().is_relative_to(output.resolve()):
        raise ValueError("Choose a public export directory separate from the study root and private files.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(directory.resolve()) + ".lock", timeout=0), FileLock(str(output.resolve()) + ".lock", timeout=0):
        if output.exists():
            raise ValueError("Export output already exists. Choose a new directory.")
        candidates = load_candidates(private, plan, config)
        if len(candidates) != len(plan["counterfactual_slots"]):
            raise ValueError("Control generation is incomplete. Resume preparation before exporting.")
        for candidate in candidates.values():
            if candidate.review.status == "rejected":
                raise ValueError("Rejected controls must be replaced and reviewed before export.")
            if config.counterfactuals.require_review and candidate.review.status != "approved":
                raise ValueError("Approve all counterfactuals in private/counterfactuals.json before exporting.")
        public = {name: [] for name in config.annotator_ids()}
        manifest = {
            "schema_version": STUDY_SCHEMA, "study_id": plan["study_id"],
            "annotation_format": ANNOTATION_FORMAT,
            "plan_sha256": fingerprint(plan), "annotators": config.annotator_ids(),
            "tasks": {}, "assignments": {}, "controls": {},
            "shared_source_keys": plan["shared_source_keys"],
        }
        for name, source_keys in plan["assignments"].items():
            for source_key in source_keys:
                analysis = Analysis.model_validate(plan["sources"][source_key])
                key = task_key(plan["study_id"], source_key, None)
                data, parts = exchange_data(analysis, key)
                public[name].append({"data": data})
                manifest["tasks"][key] = {
                    "kind": "normal", "source_key": source_key, "parts": parts,
                    "data_sha256": fingerprint(data),
                }
        for candidate in candidates.values():
            analysis = Analysis.model_validate(plan["sources"][candidate.source_key])
            key = task_key(plan["study_id"], candidate.source_key, None,
                           [candidate.candidate_id, candidate.misleading_summary])
            data, parts = exchange_data(analysis, key, {candidate.part_id: candidate.misleading_summary})
            public[candidate.assigned_annotator].append({"data": data})
            manifest["tasks"][key] = {
                "kind": "counterfactual", "source_key": candidate.source_key, "part_id": candidate.part_id,
                "parts": parts,
                "candidate_id": candidate.candidate_id, "data_sha256": fingerprint(data),
            }
            manifest["controls"][key] = {
                "part_id": candidate.part_id,
                "counter_label": candidate.counter_label.value, "review_status": candidate.review.status,
                "reference_label": candidate.review.reference_label.value if candidate.review.status == "approved" else None,
                "reviewer": candidate.review.reviewer if candidate.review.status == "approved" else None,
            }
        for name, tasks in public.items():
            random.Random(fingerprint([config.seed, name, plan["study_id"], "order"])).shuffle(tasks)
            manifest["assignments"][name] = [task["data"]["task_key"] for task in tasks]
        manifest["distribution_id"] = fingerprint(manifest)[:24]
        export_id = manifest["distribution_id"]
        staging = Path(tempfile.mkdtemp(prefix="." + output.name + "-", dir=output.parent))
        try:
            for name, tasks in public.items():
                atomic_json(staging / (name + ".json"), tasks)
            (staging / "labeling_config.xml").write_text(labeling_config(), encoding="utf-8")
            (staging / "annotation_instructions.html").write_text(annotation_instructions(), encoding="utf-8")
            manual = files("clarity_qa").joinpath("resources/CLARITY-Annotator-Guide.pdf").read_bytes()
            (staging / "CLARITY-Annotator-Guide.pdf").write_bytes(manual)
            guide = (
                "# Label Studio import\n\nDistribution: " + export_id + "\n\n"
                "Create one project per annotator. Paste labeling_config.xml into Labeling Setup, "
                "then import only that annotator's JSON file. Do not import all files into one ordinary "
                "Community Edition project: filenames do not enforce task assignment. "
                "Community Edition users can see all projects; use separate instances or Enterprise "
                "permissions if access must be isolated. Annotators should work independently and "
                "must not inspect each other's projects or annotations.\n\n"
                "Paste annotation_instructions.html into the project's Instructions field. "
                "Each task contains one complete exchange, all numbered sub-questions and their answer summaries. "
                "Annotators highlight each entire numbered answer summary and assign one response label, "
                "based on the ORIGINAL exchange. Use the Error label for a sub-question absent from the original. "
                "The corrections field accepts missing questions as Question: Label and summary-error reports. "
                "Summaries and corrections stay in the source language; response labels stay in English.\n\n"
                "After annotation, export each project in full JSON format (not JSON_MIN). "
                "Save each export under the same annotator filename in a separate exports directory. "
                "Then run evaluate-study with the coordinator's study directory and distribution ID. "
                "Do not publish this directory as the final research dataset.\n"
            )
            (staging / "IMPORT.md").write_text(guide, encoding="utf-8")
            atomic_json(private / "distributions" / (export_id + ".json"), manifest)
            staging.rename(output)
            atomic_json(private / "latest_distribution.json", {"distribution_id": export_id})
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return manifest
