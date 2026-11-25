"""Configuration and review contracts for independent annotation studies."""

import re
from typing import List, Literal, Optional, Union

from pydantic import Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from .models import Record
from .taxonomy import EvasionLabel


class OverlapConfig(Record):
    fraction: float = Field(default=0.20, ge=0, le=1, allow_inf_nan=False)
    shared_with: Literal["all"] = "all"
    unit: Literal["source_qa"] = "source_qa"


class CounterfactualConfig(Record):
    enabled: StrictBool = False
    count_per_annotator: StrictInt = Field(default=0, ge=0)
    require_review: StrictBool = True
    min_length_ratio: float = Field(default=0.5, gt=0, allow_inf_nan=False)
    max_length_ratio: float = Field(default=2.0, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def coherent_settings(self):
        if not self.enabled and self.count_per_annotator:
            raise ValueError("Enable counterfactuals or set count_per_annotator to zero.")
        if self.min_length_ratio > self.max_length_ratio:
            raise ValueError("Counterfactual length limits are reversed.")
        return self


class StudyConfig(Record):
    annotators: Union[StrictInt, List[StrictStr]] = 3
    seed: StrictInt = 42
    overlap: OverlapConfig = Field(default_factory=OverlapConfig)
    counterfactuals: CounterfactualConfig = Field(default_factory=CounterfactualConfig)
    show_model_labels: Literal[False] = False

    @field_validator("annotators")
    @classmethod
    def annotator_identifiers(cls, value):
        if isinstance(value, int):
            if value < 1:
                raise ValueError("At least one annotator is required.")
        else:
            if not value or len(value) != len(set(value)):
                raise ValueError("Annotator IDs must be non-empty and unique.")
            if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name) for name in value):
                raise ValueError("Annotator IDs must use letters, numbers, underscores or hyphens (maximum 64 characters).")
        return value

    @model_validator(mode="after")
    def enough_annotators(self):
        if len(self.annotator_ids()) == 1 and self.overlap.fraction:
            raise ValueError("Shared overlap requires at least two annotators.")
        return self

    def annotator_ids(self):
        if isinstance(self.annotators, int):
            return ["annotator_{:02d}".format(i + 1) for i in range(self.annotators)]
        return list(self.annotators)


class CounterfactualDraft(Record):
    misleading_summary: StrictStr = Field(min_length=1)


class CounterfactualReview(Record):
    status: Literal["pending", "approved", "rejected"] = "pending"
    reviewer: Optional[StrictStr] = None
    reference_label: Optional[EvasionLabel] = None
    notes: StrictStr = ""

    @model_validator(mode="after")
    def approved_reference(self):
        if self.status == "approved":
            if not self.reviewer or not self.reviewer.strip() or self.reference_label is None:
                raise ValueError("Approval requires a named reviewer and a verified reference_label.")
        return self


class CounterfactualCandidate(Record):
    candidate_id: StrictStr
    source_key: StrictStr
    part_id: StrictInt
    assigned_annotator: StrictStr
    question: StrictStr
    answer: StrictStr
    sub_question: StrictStr
    faithful_summary: StrictStr
    proposed_source_label: EvasionLabel
    counter_label: EvasionLabel
    misleading_summary: StrictStr
    generation_settings: dict
    review: CounterfactualReview = Field(default_factory=CounterfactualReview)

    @model_validator(mode="after")
    def counterlabel_differs(self):
        if self.counter_label == self.proposed_source_label:
            raise ValueError("The counterlabel must differ from the model's source label.")
        if self.review.status == "approved" and self.review.reference_label == self.counter_label:
            raise ValueError("The reviewed reference_label must differ from the counter_label.")
        return self
