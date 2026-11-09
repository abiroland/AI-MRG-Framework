"""The classification wizard: a short set of questions that routes an asset to a track."""

from __future__ import annotations

from dataclasses import dataclass

from aimrg.core.models import AICategory, Classification


@dataclass(frozen=True)
class Question:
    key: str
    prompt: str
    help: str


QUESTIONS: tuple[Question, ...] = (
    Question(
        key="ai_category",
        prompt="What kind of technique does it use?",
        help=(
            "traditional = statistical/economic/financial model (e.g. logistic scorecard); "
            "ml_non_generative = machine learning that does not generate content or act on "
            "its own (e.g. gradient boosting); generative = produces text, images or code "
            "(e.g. an LLM); agentic = plans and takes actions with some autonomy."
        ),
    ),
    Question(
        key="is_model_sr26_2",
        prompt=(
            "Is it a complex quantitative method that applies statistical, economic or "
            "financial theory to turn inputs into quantitative estimates?"
        ),
        help=(
            "This is the SR 26-2 model definition. Simple arithmetic (including spreadsheet "
            "calculations) and deterministic rule-based software are not models."
        ),
    ),
    Question(
        key="consequential_decision",
        prompt=(
            "Does it make, or substantially influence, a consequential decision about a "
            "consumer (credit, housing, insurance, employment)?"
        ),
        help="If yes, consumer-protection controls (adverse action, fair lending) apply.",
    ),
    Question(
        key="third_party",
        prompt="Is it developed or provided by a vendor or other third party?",
        help="If yes, third-party due-diligence and monitoring controls apply.",
    ),
    Question(
        key="jurisdictions",
        prompt="Which jurisdictions do its decisions affect? (e.g. US, US-CO)",
        help="Use ISO-style codes; state codes such as US-CO trigger state-law controls.",
    ),
    Question(
        key="rationale",
        prompt="Briefly explain the classification (recorded as evidence).",
        help="Especially important for borderline cases.",
    ),
)


def classify(
    *,
    ai_category: AICategory | str,
    is_model_sr26_2: bool,
    rationale: str,
    consequential_decision: bool = False,
    third_party: bool = False,
    jurisdictions: list[str] | None = None,
) -> Classification:
    """Build a :class:`Classification` from wizard answers."""
    return Classification.from_answers(
        is_model_sr26_2=is_model_sr26_2,
        ai_category=AICategory(ai_category),
        rationale=rationale,
        consequential_decision=consequential_decision,
        third_party=third_party,
        jurisdictions=[j.strip().upper() for j in (jurisdictions or []) if j.strip()],
    )
