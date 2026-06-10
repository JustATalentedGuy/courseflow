from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class ConceptRecord(TypedDict):
    concept: str
    times_asked: int
    times_correct: int
    last_score: float


class QuizState(TypedDict):
    session_id: str
    video_id: str
    user_id: str
    mode: str
    questions_asked: int
    max_questions: int
    current_difficulty: str
    all_concepts: list[str]
    asked_concepts: list[str]
    weak_concepts: list[ConceptRecord]
    current_concept: str | None
    current_question: str | None
    current_context: str | None
    user_answer: str | None
    answer_score: float | None
    answer_feedback: str | None
    key_gap: str | None
    messages: Annotated[list[Any], add_messages]
    session_complete: bool
    results: list[dict]
    times_probed: dict[str, int]
    summary: str | None


def initialise_quiz_state(
    video_id: str,
    user_id: str,
    mode: str = "quick_drill",
    all_concepts: list[str] | None = None,
    weak_concepts: list[ConceptRecord] | None = None,
    session_id: str = "",
) -> QuizState:
    concepts = list(dict.fromkeys(all_concepts or []))
    max_questions = 5 if mode == "quick_drill" else max(1, len(concepts))
    if mode == "weak_spot" and weak_concepts:
        max_questions = max(1, len(weak_concepts))
    return QuizState(
        session_id=session_id,
        video_id=video_id,
        user_id=user_id,
        mode=mode,
        questions_asked=0,
        max_questions=max_questions,
        current_difficulty="medium",
        all_concepts=concepts,
        asked_concepts=[],
        weak_concepts=weak_concepts or [],
        current_concept=None,
        current_question=None,
        current_context=None,
        user_answer=None,
        answer_score=None,
        answer_feedback=None,
        key_gap=None,
        messages=[],
        session_complete=False,
        results=[],
        times_probed={},
        summary=None,
    )
