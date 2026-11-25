"""Command-line entry points."""

import argparse
import json
import os
import sys
from pathlib import Path

from filelock import FileLock, Timeout
from pydantic import ValidationError

from .io import atomic_json, load_pairs, run_batch
from .label_studio import annotation_instructions, export_tasks, labeling_config
from .models import Analysis, BatchResult
from .pipeline import QAPipeline
from .providers import PROVIDERS, GenerationError, ProviderError
from .study import export_study, prepare_study, processed_input
from .study_evaluation import evaluate_study
from .study_models import StudyConfig
from .taxonomy import TAXONOMY_VERSION, taxonomy_records


def model_arguments(command):
    command.add_argument("--provider", choices=PROVIDERS, default=os.getenv("CLARITY_PROVIDER", "openai"))
    command.add_argument("--model", default=os.getenv("CLARITY_MODEL"), help="Provider model ID; also CLARITY_MODEL.")
    command.add_argument("--base-url", help="Base URL including /v1 for a compatible server.")
    command.add_argument("--api", choices=["responses", "chat"], help="Default: Responses for OpenAI, Chat Completions otherwise.")
    command.add_argument("--api-key-env", help="Name of an environment variable holding the provider key.")
    command.add_argument("--output-mode", choices=["auto", "json_schema", "json_object", "prompt"], default="auto")
    command.add_argument("--max-output-tokens", type=int, default=8192)
    command.add_argument("--timeout", type=float, default=120)
    command.add_argument("--max-retries", type=int, default=2, help="SDK retries for transient request failures.")
    command.add_argument("--repair-attempts", type=int, default=1, help="Extra attempts for invalid model output per stage.")


def configured_pipeline(args):
    return QAPipeline(
        model=args.model, provider=args.provider, base_url=args.base_url,
        api=args.api, output_mode=args.output_mode, api_key_env=args.api_key_env,
        max_output_tokens=args.max_output_tokens, timeout=args.timeout,
        max_retries=args.max_retries, repair_attempts=args.repair_attempts,
    )


def parser():
    root = argparse.ArgumentParser(prog="clarity-qa", description="Multilingual political QA decomposition and pre-annotation.")
    commands = root.add_subparsers(dest="command", required=True)
    analyze = commands.add_parser("analyze", help="Analyze a QA pair or a JSON batch.")
    source = analyze.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=str, help="Input JSON path, or - for stdin.")
    source.add_argument("--question", help="One complete question.")
    analyze.add_argument("--answer", help="The complete answer, used with --question.")
    analyze.add_argument("--language", help="Override input language(s); omit to use per-record language or auto detection.")
    analyze.add_argument("--output", required=True, type=Path, help="Resumable result JSON file.")
    analyze.add_argument("--resume", action="store_true", help="Reuse completed items with identical inputs/settings.")
    model_arguments(analyze)
    export = commands.add_parser("export-label-studio", help="Export completed results and a review template.")
    export.add_argument("--input", required=True, type=Path)
    export.add_argument("--output", required=True, type=Path, help="Label Studio tasks JSON.")
    export.add_argument("--config", type=Path, help="Optional destination for the XML labeling config.")
    export.add_argument("--instructions", type=Path, help="Optional destination for the HTML annotation instructions.")
    export.add_argument("--show-model-labels", action="store_true", help="Include model highlight predictions for coordinator review. Leave off for human annotation.")
    study = commands.add_parser("prepare-study", help="Plan grouped annotation overlap and generate private control candidates.")
    study.add_argument("--input", required=True, type=Path, help="Raw QA JSON or completed analysis JSON.")
    study.add_argument("--config", required=True, type=Path, help="Study configuration JSON.")
    study.add_argument("--output", required=True, type=Path, help="New coordinator study directory.")
    study.add_argument("--resume", action="store_true", help="Resume with identical source, configuration and model settings.")
    model_arguments(study)
    distribution = commands.add_parser("export-study", help="Export a blinded Label Studio pack after control review.")
    distribution.add_argument("--study", required=True, type=Path)
    distribution.add_argument("--output", required=True, type=Path, help="New public-only import directory.")
    evaluation = commands.add_parser("evaluate-study", help="Merge per-annotator exports and report agreement and control metrics.")
    evaluation.add_argument("--study", required=True, type=Path)
    evaluation.add_argument("--exports", required=True, type=Path, help="Directory of full JSON exports named after annotators.")
    evaluation.add_argument("--distribution", help="Distribution ID; defaults to the latest export from this study.")
    evaluation.add_argument("--output", required=True, type=Path, help="New evaluation report JSON.")
    commands.add_parser("taxonomy", help="Print the fixed English labels and explanations.")
    return root


def read_json(path):
    if path == "-":
        return json.load(sys.stdin)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def analyze_command(args):
    if args.input is not None:
        if args.answer is not None:
            raise ValueError("Use --answer only with --question.")
        if args.input != "-" and Path(args.input).resolve() == args.output.resolve():
            raise ValueError("Input and output paths must differ.")
        source = read_json(args.input)
    else:
        if args.answer is None:
            raise ValueError("--question requires --answer.")
        source = {"question": args.question, "answer": args.answer}
    pairs = load_pairs(source, args.language)
    with configured_pipeline(args) as pipeline:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(args.output.resolve()) + ".lock", timeout=0):
            if args.output.exists() and not args.resume:
                raise ValueError("Output already exists. Use --resume or choose a new output path.")
            report = run_batch(
                pairs, pipeline, args.output, resume=args.resume,
                progress=lambda done, total: print("Completed {}/{}".format(done, total), file=sys.stderr),
            )
    print("Saved {} exchange(s) to {}".format(len(report.results), args.output), file=sys.stderr)


def export_command(args):
    if args.input.resolve() == args.output.resolve():
        raise ValueError("Input and export output paths must differ.")
    paths = [args.input, args.output] + [p for p in (args.config, args.instructions) if p is not None]
    if len({p.resolve() for p in paths}) != len(paths):
        raise ValueError("Input, task JSON, XML and instructions paths must all differ.")
    value = read_json(str(args.input))
    if isinstance(value, dict) and "results" in value:
        report = BatchResult.model_validate(value)
        analyses = report.results
        if report.errors:
            print("Exporting completed items only; the input contains failed items.", file=sys.stderr)
    elif isinstance(value, list):
        analyses = [Analysis.model_validate(item) for item in value]
    else:
        analyses = [Analysis.model_validate(value)]
    tasks = export_tasks(analyses, show_model_labels=args.show_model_labels)
    atomic_json(args.output, tasks)
    if args.config:
        args.config.parent.mkdir(parents=True, exist_ok=True)
        args.config.write_text(labeling_config(), encoding="utf-8")
    if args.instructions:
        args.instructions.parent.mkdir(parents=True, exist_ok=True)
        args.instructions.write_text(annotation_instructions(), encoding="utf-8")
    print("Exported {} complete-exchange task(s).".format(len(tasks)), file=sys.stderr)


def study_command(args):
    output = args.output.resolve()
    if any(path.resolve() == output or path.resolve().is_relative_to(output) for path in (args.input, args.config)):
        raise ValueError("Study input and config must be outside the study output directory.")
    value = read_json(str(args.input))
    config = StudyConfig.model_validate(read_json(str(args.config)))
    needs_model = processed_input(value) is None or (config.counterfactuals.enabled and config.counterfactuals.count_per_annotator > 0)
    if needs_model:
        # Validate raw inputs before constructing a provider or making a request.
        if processed_input(value) is None:
            load_pairs(value)
        with configured_pipeline(args) as pipeline:
            plan = prepare_study(value, config, args.output, pipeline=pipeline, resume=args.resume)
    else:
        plan = prepare_study(value, config, args.output, resume=args.resume)
    print("Prepared {} source exchange(s), {} shared, for {} annotator(s).".format(
        len(plan["sources"]), len(plan["shared_source_keys"]), len(config.annotator_ids())), file=sys.stderr)
    print("Review {} control candidate(s) in {}/private/counterfactuals.json, then run export-study.".format(
        len(plan["counterfactual_slots"]), args.output), file=sys.stderr)


def distribute_command(args):
    manifest = export_study(args.study, args.output)
    print("Exported distribution {} to {}.".format(manifest["distribution_id"], args.output), file=sys.stderr)
    for name, keys in manifest["assignments"].items():
        print("{}: {} task(s)".format(name, len(keys)), file=sys.stderr)


def evaluate_command(args):
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(args.output.resolve()) + ".lock", timeout=0):
        if args.output.exists():
            raise ValueError("Evaluation output already exists. Choose a new path.")
        report = evaluate_study(args.study, args.exports, args.distribution)
        atomic_json(args.output, report)
    print("Saved evaluation and {} normal annotation unit(s) to {}.".format(
        len(report["normal_annotations"]), args.output), file=sys.stderr)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "analyze":
            analyze_command(args)
        elif args.command == "export-label-studio":
            export_command(args)
        elif args.command == "prepare-study":
            study_command(args)
        elif args.command == "export-study":
            distribute_command(args)
        elif args.command == "evaluate-study":
            evaluate_command(args)
        else:
            print(json.dumps({"version": TAXONOMY_VERSION, "labels": taxonomy_records()}, indent=2))
    except Timeout:
        print("Error: another process is writing this output. Choose another path or wait for it to finish.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted. Completed items remain saved; rerun with --resume.", file=sys.stderr)
        return 130
    except (OSError, ValueError, ValidationError, GenerationError, ProviderError) as error:
        print("Error: {}".format(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
