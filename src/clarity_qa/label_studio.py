"""Export model predictions with original context for Label Studio review."""

from importlib.resources import files

from .taxonomy import TAXONOMY


def export_tasks(analyses):
    tasks = []
    for result in analyses:
        for part in result.sub_questions:
            tasks.append({
                "data": {
                    "source_id": result.id,
                    "input_index": result.input_index,
                    "part_id": part.id,
                    "language": result.language,
                    "metadata": result.metadata,
                    "question": result.question,
                    "answer": result.answer,
                    "question_summary": result.question_summary,
                    "answer_summary": result.answer_summary,
                    "part_title": part.title,
                    "sub_question": part.question,
                    "sub_answer": part.answer_summary,
                    "evidence": "\n\n".join(span.text for span in part.answer_evidence),
                    "taxonomy_definitions": "\n\n".join(
                        "{} - {}".format(label.value, definition)
                        for label, (_, definition) in TAXONOMY.items()
                    ),
                },
                "predictions": [{
                    "model_version": result.provenance.model + "/" + result.provenance.prompt_version,
                    "result": [{
                        "from_name": "evasion",
                        "to_name": "sub_question",
                        "type": "choices",
                        "value": {"choices": [part.evasion_label.value]},
                    }],
                }],
            })
    return tasks


def labeling_config():
    return files("clarity_qa").joinpath("templates/label_studio.xml").read_text(encoding="utf-8")
