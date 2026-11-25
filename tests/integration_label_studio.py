"""Optional integration against an installed Label Studio application and SQLite.

Run with PYTHONPATH=src:tests in an isolated Label Studio test environment.
This test uses the application's real project/import/annotation/export API views.
It does not contact a model provider or an existing Label Studio deployment.
"""

import json
import os
import sys
import tempfile
from pathlib import Path


def run():
    with tempfile.TemporaryDirectory(prefix="clarity-label-studio-") as temporary:
        root = Path(temporary)
        os.environ.update({
            "LABEL_STUDIO_BASE_DATA_DIR": str(root / "server"),
            "COLLECT_ANALYTICS": "false", "SENTRY_DSN": "", "FRONTEND_SENTRY_DSN": "",
            "LOG_LEVEL": "ERROR", "LABEL_STUDIO_HOST": "http://testserver",
            "LATEST_VERSION_CHECK": "false",
        })
        import label_studio
        from label_studio.server import _setup_env
        _setup_env()
        from django.conf import settings
        from django.core.management import call_command
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.db import connections
        from rest_framework.test import APIClient
        from users.models import User
        from organizations.models import Organization
        from tasks.models import Task

        from clarity_qa import QAPipeline
        from clarity_qa.io import atomic_json
        from clarity_qa.study import export_study, prepare_study, read_json
        from clarity_qa.study_evaluation import evaluate_study
        from test_study import ControlBackend, analyses, annotation, approve, configuration

        settings.ALLOWED_HOSTS = ["testserver"]
        call_command("migrate", verbosity=0, interactive=False)
        user = User.objects.create_user(email="coordinator@example.invalid", password="local-fixture-only")
        organization = Organization.create_organization(created_by=user, title="CLARITY local integration")
        user.active_organization = organization
        user.save()
        client = APIClient()
        client.force_authenticate(user=user)

        study, pack, exports = root / "study", root / "pack", root / "exports"
        exports.mkdir()
        values = [item.model_dump(mode="json") for item in analyses(20)]
        plan = prepare_study(values, configuration(2), study, pipeline=QAPipeline(backend=ControlBackend()))
        approve(study)
        manifest = export_study(study, pack)
        xml = (pack / "labeling_config.xml").read_text()
        imported, annotated = 0, 0
        for name in manifest["annotators"]:
            instructions = (pack / "annotation_instructions.html").read_text()
            response = client.post("/api/projects/", {"title": name, "label_config": xml,
                                   "expert_instruction": instructions}, format="json")
            assert response.status_code == 201, (response.status_code, response.content.decode()[:2000])
            assert response.json()["expert_instruction"] == instructions
            project_id = response.json()["id"]
            upload = SimpleUploadedFile(name + ".json", (pack / (name + ".json")).read_bytes(), content_type="application/json")
            response = client.post("/api/projects/{}/import".format(project_id), {"file": upload}, format="multipart")
            assert response.status_code in (200, 201), (response.status_code, response.content.decode()[:2000])
            tasks = list(Task.objects.filter(project_id=project_id).order_by("id"))
            assert len(tasks) == len(manifest["assignments"][name])
            imported += len(tasks)
            for task in tasks:
                key = task.data["task_key"]
                info = manifest["tasks"][key]
                result = annotation(data=task.data)["result"]
                source = plan["sources"][info["source_key"]]
                for region, span in zip(result, info["parts"]):
                    label = next(part["evasion_label"] for part in source["sub_questions"] if part["id"] == span["part_id"])
                    if key in manifest["controls"] and span["part_id"] == manifest["controls"][key]["part_id"]:
                        label = manifest["controls"][key]["reference_label"]
                    region["value"]["labels"] = [label]
                response = client.post("/api/tasks/{}/annotations/".format(task.id), {"result": result, "lead_time": 1.0}, format="json")
                assert response.status_code == 201, (response.status_code, response.content.decode()[:2000])
                annotated += 1
            response = client.get("/api/projects/{}/export?exportType=JSON&download_all_tasks=true".format(project_id))
            assert response.status_code == 200, (response.status_code, response.content.decode()[:2000])
            raw = b"".join(response.streaming_content) if response.streaming else response.content
            atomic_json(exports / (name + ".json"), json.loads(raw))
        report = evaluate_study(study, exports)
        assert len(report["normal_annotations"]) == 40
        assert report["agreement"]["evasion"]["items"] == 8
        assert report["agreement"]["evasion"]["kappa"] == 1
        assert report["agreement"]["clarity"]["kappa"] == 1
        assert all(scores["summary_error_detection_rate"] is None for scores in report["counterfactuals"].values())
        assert all(scores["reviewed_source_label_accuracy"] == 1 for scores in report["counterfactuals"].values())
        receipt = {
            "label_studio_version": label_studio.__version__, "projects": 3,
            "imported_tasks": imported, "submitted_annotations": annotated,
            "normal_annotation_units": 40, "shared_annotation_units": 8, "control_exposures": 6,
            "highlight_regions": sum(v["completed_subquestions"] for v in report["completion"].values()),
            "saved_instruction_css_preserved": True,
            "evasion_kappa": 1, "clarity_kappa": 1,
            "claim_boundary": "Real Label Studio API views and SQLite, synthetic annotations, no browser or live model inference.",
        }
        connections.close_all()
        print(json.dumps(receipt))


if __name__ == "__main__":
    run()
