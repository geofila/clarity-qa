"""Input, model response, and public output contracts."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_validator

from .taxonomy import CLARITY_EXPLANATIONS, TAXONOMY, ClarityLabel, EvasionLabel


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QAPair(Record):
    id: Optional[Union[StrictStr, StrictInt]] = None
    question: StrictStr = Field(min_length=1)
    answer: StrictStr = Field(min_length=1)
    language: StrictStr = "auto"
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("question", "answer", "language")
    @classmethod
    def meaningful_text(cls, value):
        if not value.strip():
            raise ValueError("Text must contain a non-whitespace character.")
        return value


class QuestionPart(Record):
    id: StrictInt
    title: str
    question: str


class Decomposition(Record):
    language: str
    question_summary: str
    sub_questions: List[QuestionPart]


class AnswerPart(Record):
    id: StrictInt
    answer_summary: str
    evidence: List[str]


class AnswerSummaries(Record):
    answer_summary: str
    sub_answers: List[AnswerPart]


class LabelPrediction(Record):
    id: StrictInt
    evasion_label: EvasionLabel


class LabelPredictions(Record):
    predictions: List[LabelPrediction]


class Evidence(Record):
    text: str
    start: int
    end: int


class AnnotatedQuestion(Record):
    id: int
    title: str
    question: str
    answer_summary: str
    answer_evidence: List[Evidence]
    evasion_label: EvasionLabel
    clarity_label: ClarityLabel
    label_explanation: str
    clarity_explanation: str

    @model_validator(mode="after")
    def canonical_taxonomy(self):
        clarity, explanation = TAXONOMY[self.evasion_label]
        if (
            self.clarity_label != clarity
            or self.label_explanation != explanation
            or self.clarity_explanation != CLARITY_EXPLANATIONS[clarity]
        ):
            raise ValueError("Labels and explanations must match the fixed taxonomy.")
        return self


class Provenance(Record):
    package_version: str
    prompt_version: str
    taxonomy_version: str
    provider: str
    model: str
    api: str
    output_modes: List[str]
    created_at: datetime
    review_status: Literal["unreviewed"] = "unreviewed"


class Analysis(Record):
    input_index: Optional[int] = None
    id: Optional[Union[StrictStr, StrictInt]]
    question: str
    answer: str
    language: str
    metadata: Dict[str, Any]
    question_summary: str
    answer_summary: str
    sub_questions: List[AnnotatedQuestion]
    provenance: Provenance

    @model_validator(mode="after")
    def original_answer_evidence(self):
        ids = [part.id for part in self.sub_questions]
        if ids != list(range(1, len(ids) + 1)):
            raise ValueError("Sub-question IDs must be consecutive, starting at 1.")
        for part in self.sub_questions:
            for span in part.answer_evidence:
                if not span.text or span.start < 0 or span.end <= span.start:
                    raise ValueError("Evidence must be a non-empty span.")
                if self.answer[span.start:span.end] != span.text:
                    raise ValueError("Evidence offsets must match the original answer.")
        return self


class ItemError(Record):
    input_index: int
    id: Optional[Union[StrictStr, StrictInt]]
    message: str


class BatchResult(Record):
    schema_version: Literal["1.0"] = "1.0"
    input_sha256: str
    settings_sha256: str
    results: List[Analysis] = Field(default_factory=list)
    errors: List[ItemError] = Field(default_factory=list)
