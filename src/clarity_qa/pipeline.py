"""Decompose, summarize, then classify against the original exchange."""

import json
from datetime import datetime, timezone

from pydantic import ValidationError

from . import __version__
from . import prompts
from .models import (
    Analysis, AnnotatedQuestion, AnswerSummaries, Decomposition, Evidence,
    LabelPredictions, Provenance, QAPair,
)
from .providers import GenerationError, ModelBackend
from .taxonomy import CLARITY_EXPLANATIONS, TAXONOMY, TAXONOMY_VERSION


def check_ids(records, expected):
    if [record.id for record in records] != expected:
        raise ValueError("Part IDs must match the supplied IDs in the original order, without duplicates.")


def check_decomposition(value):
    check_ids(value.sub_questions, list(range(1, len(value.sub_questions) + 1)))
    texts = [value.language, value.question_summary]
    for part in value.sub_questions:
        texts.extend([part.title, part.question])
    if any(not text.strip() for text in texts):
        raise ValueError("Language, summaries, titles, and questions must contain text.")
    if value.language.lower() == "auto":
        raise ValueError("Return the identified language instead of 'auto'.")


def check_summaries(value, questions, original_answer):
    check_ids(value.sub_answers, [part.id for part in questions])
    if not value.answer_summary.strip():
        raise ValueError("The overall answer summary must contain text.")
    for part in value.sub_answers:
        if not part.answer_summary.strip():
            raise ValueError("Each sub-answer needs a summary, including unanswered parts.")
        for quote in part.evidence:
            if not quote.strip() or quote not in original_answer:
                raise ValueError("Every evidence quote must be a non-empty exact substring of the original answer.")


class QAPipeline:
    """Analyze individual exchanges. Reuse one instance for a sequential batch."""

    def __init__(self, model=None, provider="openai", repair_attempts=1, backend=None, **options):
        if repair_attempts < 0:
            raise ValueError("repair_attempts must be non-negative.")
        self.backend = backend if backend is not None else ModelBackend(model=model, provider=provider, **options)
        self.repair_attempts = repair_attempts

    def _stage(self, name, system, payload, response_model, validate):
        user = json.dumps(payload, ensure_ascii=False)
        feedback = ""
        for attempt in range(self.repair_attempts + 1):
            try:
                raw = self.backend.generate(system, user + feedback, response_model.model_json_schema())
                value = response_model.model_validate(raw)
                validate(value)
                return value
            except ValidationError as error:
                details = error.errors(include_input=False, include_url=False)
                reason = json.dumps(details, default=str)
            except (GenerationError, ValueError) as error:
                reason = str(error)
            if attempt == self.repair_attempts:
                raise GenerationError("{} failed validation after {} attempt(s): {}".format(name, attempt + 1, reason))
            feedback = "\nThe previous attempt failed validation: {}\nRegenerate the complete JSON object and fix these errors.".format(reason)
        raise GenerationError("{} did not complete.".format(name))

    def analyze(self, question, answer=None, *, language=None, id=None, metadata=None):
        """Accept a QAPair, a mapping, or question/answer strings."""
        if isinstance(question, (dict, QAPair)):
            if answer is not None or id is not None or metadata is not None:
                raise ValueError("Pass either a QA object or separate question/answer fields.")
            pair = QAPair.model_validate(question)
            if language is not None:
                pair = QAPair.model_validate({**pair.model_dump(), "language": language})
        else:
            pair = QAPair(question=question, answer=answer, language=language or "auto", id=id, metadata=metadata or {})
        payload = {"question": pair.question, "answer": pair.answer, "language": pair.language}
        first_mode = len(self.backend.output_modes)
        decomposition = self._stage("decomposition", prompts.DECOMPOSE, payload, Decomposition, check_decomposition)
        questions = decomposition.sub_questions
        payload["language"] = pair.language if pair.language != "auto" else decomposition.language
        payload["sub_questions"] = [part.model_dump() for part in questions]
        summaries = self._stage(
            "answer summaries", prompts.SUMMARIZE, payload, AnswerSummaries,
            lambda value: check_summaries(value, questions, pair.answer),
        )
        predictions = []
        if questions:
            payload["answer_summaries"] = summaries.model_dump()
            labels = self._stage(
                "classification", prompts.CLASSIFY, payload, LabelPredictions,
                lambda value: check_ids(value.predictions, [part.id for part in questions]),
            )
            predictions = labels.predictions
        annotated = []
        for question_part, answer_part, prediction in zip(questions, summaries.sub_answers, predictions):
            label = prediction.evasion_label
            clarity, explanation = TAXONOMY[label]
            evidence = []
            for quote in answer_part.evidence:
                start = pair.answer.index(quote)
                evidence.append(Evidence(text=quote, start=start, end=start + len(quote)))
            annotated.append(AnnotatedQuestion(
                id=question_part.id,
                title=question_part.title,
                question=question_part.question,
                answer_summary=answer_part.answer_summary,
                answer_evidence=evidence,
                evasion_label=label,
                clarity_label=clarity,
                label_explanation=explanation,
                clarity_explanation=CLARITY_EXPLANATIONS[clarity],
            ))
        return Analysis(
            id=pair.id,
            question=pair.question,
            answer=pair.answer,
            language=payload["language"],
            metadata=pair.metadata,
            question_summary=decomposition.question_summary,
            answer_summary=summaries.answer_summary,
            sub_questions=annotated,
            provenance=Provenance(
                package_version=__version__,
                prompt_version=prompts.PROMPT_VERSION,
                taxonomy_version=TAXONOMY_VERSION,
                provider=self.backend.provider,
                model=self.backend.model,
                api=self.backend.api,
                output_modes=self.backend.output_modes[first_mode:],
                created_at=datetime.now(timezone.utc),
            ),
        )

    def settings(self):
        return {
            **self.backend.settings(),
            "repair_attempts": self.repair_attempts,
            "package_version": __version__,
            "prompt_version": prompts.PROMPT_VERSION,
            "taxonomy_version": TAXONOMY_VERSION,
        }

    def close(self):
        self.backend.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
