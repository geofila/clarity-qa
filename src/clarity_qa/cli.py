"""Command-line entry points."""

import argparse
import json
import os
import sys
from pathlib import Path

from filelock import FileLock, Timeout
from pydantic import ValidationError

from .io import atomic_json, load_pairs, run_batch
from .label_studio import export_tasks, labeling_config
from .models import Analysis, BatchResult
from .pipeline import QAPipeline
from .providers import PROVIDERS, GenerationError, ProviderError
from .taxonomy import TAXONOMY_VERSION, taxonomy_records


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
    analyze.add_argument("--provider", choices=PROVIDERS, default=os.getenv("CLARITY_PROVIDER", "openai"))
    analyze.add_argument("--model", default=os.getenv("CLARITY_MODEL"), help="Provider model ID; also CLARITY_MODEL.")
    analyze.add_argument("--base-url", help="Base URL including /v1 for a compatible server.")
    analyze.add_argument("--api", choices=["responses", "chat"], help="Default: Responses for OpenAI, Chat Completions otherwise.")
    analyze.add_argument("--api-key-env", help="Name of an environment variable holding the provider key.")
    analyze.add_argument("--output-mode", choices=["auto", "json_schema", "json_object", "prompt"], default="auto")
    analyze.add_argument("--max-output-tokens", type=int, default=8192)
    analyze.add_argument("--timeout", type=float, default=120)
    analyze.add_argument("--max-retries", type=int, default=2, help="SDK retries for transient request failures.")
    analyze.add_argument("--repair-attempts", type=int, default=1, help="Extra attempts for invalid model output per stage.")
    export = commands.add_parser("export-label-studio", help="Export completed results and a review template.")
    export.add_argument("--input", required=True, type=Path)
    export.add_argument("--output", required=True, type=Path, help="Label Studio tasks JSON.")
    export.add_argument("--config", type=Path, help="Optional destination for the XML labeling config.")
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
    with QAPipeline(
        model=args.model, provider=args.provider, base_url=args.base_url,
        api=args.api, output_mode=args.output_mode, api_key_env=args.api_key_env,
        max_output_tokens=args.max_output_tokens, timeout=args.timeout,
        max_retries=args.max_retries, repair_attempts=args.repair_attempts,
    ) as pipeline:
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
    if args.config and args.config.resolve() in (args.input.resolve(), args.output.resolve()):
        raise ValueError("The XML config path must differ from the JSON paths.")
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
    tasks = export_tasks(analyses)
    atomic_json(args.output, tasks)
    if args.config:
        args.config.parent.mkdir(parents=True, exist_ok=True)
        args.config.write_text(labeling_config(), encoding="utf-8")
    print("Exported {} sub-question task(s).".format(len(tasks)), file=sys.stderr)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "analyze":
            analyze_command(args)
        elif args.command == "export-label-studio":
            export_command(args)
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
