"""The nine released QEvasion labels and their fixed English definitions."""

from enum import Enum
from types import MappingProxyType

TAXONOMY_VERSION = "qevasion-2024-nine-labels-v1"


class EvasionLabel(str, Enum):
    EXPLICIT = "Explicit"
    IMPLICIT = "Implicit"
    DODGING = "Dodging"
    DEFLECTION = "Deflection"
    PARTIAL = "Partial/half-answer"
    GENERAL = "General"
    DECLINING = "Declining to answer"
    IGNORANCE = "Claims ignorance"
    CLARIFICATION = "Clarification"


class ClarityLabel(str, Enum):
    CLEAR_REPLY = "Clear Reply"
    AMBIVALENT = "Ambivalent"
    CLEAR_NON_REPLY = "Clear Non-Reply"


TAXONOMY = MappingProxyType({
    EvasionLabel.EXPLICIT: (
        ClarityLabel.CLEAR_REPLY,
        "The requested information is explicitly stated in the requested form.",
    ),
    EvasionLabel.IMPLICIT: (
        ClarityLabel.AMBIVALENT,
        "The requested information is conveyed indirectly and requires inference.",
    ),
    EvasionLabel.DODGING: (
        ClarityLabel.AMBIVALENT,
        "The response ignores the question and addresses another topic.",
    ),
    EvasionLabel.DEFLECTION: (
        ClarityLabel.AMBIVALENT,
        "The response begins on topic, then shifts focus away from the requested information.",
    ),
    EvasionLabel.PARTIAL: (
        ClarityLabel.AMBIVALENT,
        "The response provides only part of the requested information.",
    ),
    EvasionLabel.GENERAL: (
        ClarityLabel.AMBIVALENT,
        "The response is too general to provide the requested specificity.",
    ),
    EvasionLabel.DECLINING: (
        ClarityLabel.CLEAR_NON_REPLY,
        "The speaker acknowledges the question and refuses to answer it.",
    ),
    EvasionLabel.IGNORANCE: (
        ClarityLabel.CLEAR_NON_REPLY,
        "The speaker states that they do not know the requested information.",
    ),
    EvasionLabel.CLARIFICATION: (
        ClarityLabel.CLEAR_NON_REPLY,
        "The speaker asks for clarification instead of providing the requested information.",
    ),
})

CLARITY_EXPLANATIONS = MappingProxyType({
    ClarityLabel.CLEAR_REPLY: "The response provides the requested information unambiguously.",
    ClarityLabel.AMBIVALENT: "The response is indirect, incomplete, overly general, or shifts away from the question.",
    ClarityLabel.CLEAR_NON_REPLY: "The speaker openly withholds an answer, claims ignorance, or seeks clarification.",
})


def taxonomy_records():
    """Return a JSON-serializable copy in the order used in Figure 20."""
    return [
        {
            "evasion_label": label.value,
            "clarity_label": clarity.value,
            "label_explanation": explanation,
            "clarity_explanation": CLARITY_EXPLANATIONS[clarity],
        }
        for label, (clarity, explanation) in TAXONOMY.items()
    ]
