"""Run the standard CLARITY CLI and export one complete exchange per task."""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import clarity_qa

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reuse-analysis", action="store_true", help="Use the committed live-model output without an API call.")
    parser.add_argument("--analysis", type=Path, help="Existing saved analysis, or destination for a new live analysis.")
    parser.add_argument("--output", type=Path, default=HERE / "generated", help="A new public export directory.")
    args = parser.parse_args()
    analysis = args.analysis or HERE / ("analysis.output.json" if args.reuse_analysis else "analysis.generated.json")
    analysis, output = analysis.resolve(), args.output.resolve()
    study = output.with_name(output.name + "-study")
    if output.exists() or study.exists():
        parser.error("Choose a new --output path; existing exports and study data are never overwritten.")
    if args.reuse_analysis and not analysis.is_file():
        parser.error("--reuse-analysis requires a saved analysis JSON file.")
    if not args.reuse_analysis and not os.environ.get("OPENAI_API_KEY"):
        parser.error("Set OPENAI_API_KEY, or use --reuse-analysis for an offline export.")
    if not args.reuse_analysis and analysis.exists():
        parser.error("Choose a new --analysis path, or use --reuse-analysis for the saved output.")
    commands = []
    if not args.reuse_analysis:
        commands.append([sys.executable, "-m", "clarity_qa", "analyze", "--provider", "openai",
                         "--model", os.environ.get("CLARITY_MODEL", "gpt-6.1-sol"), "--language", "en",
                         "--input", str(HERE / "input.json"), "--output", str(analysis)])
    commands += [
        [sys.executable, "-m", "clarity_qa", "prepare-study", "--input", str(analysis),
         "--config", str(HERE / "study.config.json"), "--output", str(study)],
        [sys.executable, "-m", "clarity_qa", "export-study", "--study", str(study), "--output", str(output)],
    ]
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for command in commands:
        print("COMMAND: " + json.dumps(command), flush=True)
        subprocess.run(command, check=True)
    result = json.loads(analysis.read_text())
    tasks = json.loads((output / "annotator_01.json").read_text())
    if result.get("errors") or len(result["results"]) != 3 or len(tasks) != 3:
        raise ValueError("All three original exchanges must complete before this demo is ready.")
    receipt = {
        "status": "completed", "package_version": clarity_qa.__version__,
        "annotation_format": "exchange-spans-v1", "source_exchanges": 3,
        "sub_question_units": sum(len(item["sub_questions"]) for item in result["results"]),
        "imported_exchange_tasks": len(tasks), "annotators": 1,
        "overlap_fraction": 0, "counterfactuals_enabled": False,
        "reused_saved_model_output": args.reuse_analysis,
        "analysis_sha256": hashlib.sha256(analysis.read_bytes()).hexdigest(),
        "commands": commands, "started_at_utc": started,
        "finished_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "model_provenance": [item["provenance"] for item in result["results"]],
    }
    (output / "run_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("Ready: three complete-exchange tasks, XML, HTML instructions and PDF manual in " + str(output))


if __name__ == "__main__":
    main()
