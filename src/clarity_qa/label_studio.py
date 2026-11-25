"""One complete exchange per task, with numbered answer-summary highlights."""

from importlib.resources import files

from .io import fingerprint
from .taxonomy import TAXONOMY

ANNOTATION_FORMAT = "exchange-spans-v1"
ERROR_LABEL = "Error: Question does not exist in the original text"


def utf16_length(text):
    """Label Studio browser offsets count UTF-16 code units, including emoji."""
    return len(text.encode("utf-16-le")) // 2


def utf16_slice(text, start, end):
    """Reject invalid offsets and slices that split a surrogate pair."""
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= utf16_length(text):
        raise ValueError("Invalid Label Studio text offsets.")
    try:
        return text.encode("utf-16-le")[2 * start:2 * end].decode("utf-16-le")
    except UnicodeDecodeError as error:
        raise ValueError("Label Studio offsets split a Unicode character.") from error


def taxonomy_definitions():
    definitions = "\n\n".join(
        "{} - {}".format(label.value, definition)
        for label, (_, definition) in TAXONOMY.items()
    )
    return definitions + "\n\n" + ERROR_LABEL + " - The proposed sub-question is absent from the original question. This flags a decomposition error, not a response type."


def exchange_data(analysis, key, summary_overrides=None):
    """Build public text and a private mapping from paragraphs to source parts."""
    overrides = summary_overrides or {}
    if set(overrides) - {part.id for part in analysis.sub_questions}:
        raise ValueError("A replacement summary refers to an unknown sub-question.")
    questions, answers, parts = [], [], []
    offset = 0
    for number, part in enumerate(analysis.sub_questions, 1):
        questions.append("{}. {}".format(number, " ".join(part.question.splitlines())))
        title = " ".join(part.title.splitlines())
        summary = " ".join(overrides.get(part.id, part.answer_summary).splitlines())
        paragraph = "{}. {}: {}".format(number, title, summary)
        if answers:
            offset += 2  # The two newline characters separating paragraphs.
        end = offset + utf16_length(paragraph)
        parts.append({"part_id": part.id, "number": number, "start": offset, "end": end})
        answers.append(paragraph)
        offset = end
    return {
        "task_key": key, "language": analysis.language,
        "question": analysis.question, "answer": analysis.answer,
        "sub_questions": "\n\n".join(questions), "sub_answers": "\n\n".join(answers),
        "taxonomy_definitions": taxonomy_definitions(),
    }, parts


def export_tasks(analyses, show_model_labels=False):
    """Export grouped tasks; model pre-annotations require an explicit opt-in."""
    tasks = []
    for index, result in enumerate(analyses):
        if not result.sub_questions:
            continue
        key = fingerprint([index, result.id, result.question, result.answer])[:32]
        data, parts = exchange_data(result, key)
        task = {"data": data}
        if show_model_labels:
            task["predictions"] = [{
                "model_version": result.provenance.model + "/" + result.provenance.prompt_version,
                "result": [{
                    "id": "part-{}".format(span["part_id"]),
                    "from_name": "evasion", "to_name": "sub_answers", "type": "labels",
                    "value": {"start": span["start"], "end": span["end"],
                              "text": utf16_slice(data["sub_answers"], span["start"], span["end"]),
                              "labels": [part.evasion_label.value]},
                } for span, part in zip(parts, result.sub_questions)],
            }]
        tasks.append(task)
    return tasks


def labeling_config():
    return files("clarity_qa").joinpath("templates/study_label_studio.xml").read_text(encoding="utf-8")


def annotation_instructions():
    return files("clarity_qa").joinpath("templates/annotation_instructions.html").read_text(encoding="utf-8")
