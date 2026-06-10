import json
import re
from typing import Any

import structlog
from langchain_groq import ChatGroq
from langgraph.graph import END, StateGraph

from app.core.config import settings
from app.core.exceptions import PermanentAPIError, TemporaryAPIError
from app.services.embedder import embed_texts
from app.services.external_api import call_external_sync
from app.services.quiz.state import ConceptRecord, QuizState

logger = structlog.get_logger()


def select_next_concept(state: QuizState) -> dict:
    asked = set(state["asked_concepts"])
    for record in sorted(state["weak_concepts"], key=lambda item: item["last_score"]):
        if record["concept"] not in asked:
            return {"current_concept": record["concept"], "current_context": None}
    for concept in state["all_concepts"]:
        if concept not in asked:
            return {"current_concept": concept, "current_context": None}
    if state["results"]:
        weakest = min(state["results"], key=lambda item: item["score"])
        return {"current_concept": weakest["concept"], "current_context": None}
    return {"session_complete": True, "current_concept": None}


def retrieve_concept_context(state: QuizState) -> dict:
    return {"current_context": state.get("current_context") or ""}


def _question_prompt(state: QuizState) -> str:
    return (
        "Create one concise Socratic quiz question grounded only in the context. "
        f"Concept: {state['current_concept']}. Difficulty: {state['current_difficulty']}. "
        f"Context: {state.get('current_context') or ''}. "
        "Return only the question, with no answer or preamble."
    )


def _fallback_question(state: QuizState) -> str:
    concept = state.get("current_concept") or "this concept"
    gap = state.get("key_gap")
    if gap:
        return f"How would you correct or expand your explanation of {gap} in {concept}?"
    difficulty = state["current_difficulty"]
    if difficulty == "easy":
        return f"What is {concept}, in your own words?"
    if difficulty == "hard":
        return f"What would happen if a key assumption behind {concept} changed, and why?"
    return f"Explain how {concept} works and why it is useful."


def generate_question_llm(state: QuizState) -> str:
    if settings.groq_api_key and settings.groq_api_key != "your_groq_key_here":
        try:
            response = call_external_sync(
                lambda: ChatGroq(
                    api_key=settings.groq_api_key,
                    model="llama-3.3-70b-versatile",
                    temperature=0.3,
                ).invoke(_question_prompt(state)),
                "Groq quiz question",
            )
            question = str(response.content).strip()
            if question:
                return question
        except (TemporaryAPIError, PermanentAPIError) as exc:
            logger.warning("quiz.groq.fallback", error_type=type(exc).__name__)
    return _fallback_question(state)


def generate_question(state: QuizState) -> dict:
    question = generate_question_llm(state)
    return {
        "current_question": question,
        "messages": [{"role": "assistant", "content": question}],
    }


def await_user_answer(state: QuizState) -> dict:
    return {}


def _parse_evaluation(content: str) -> dict[str, Any] | None:
    cleaned = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.IGNORECASE).strip()
    try:
        payload = json.loads(cleaned)
        score = max(0.0, min(1.0, float(payload["score"])))
        return {
            "score": score,
            "feedback": str(payload["feedback"]).strip(),
            "key_gap": payload.get("key_gap"),
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


_SEMANTIC_TOKEN_ALIASES = {
    "array": "space",
    "arrays": "space",
    "divide": "halve",
    "divided": "halve",
    "divides": "halve",
    "dividing": "halve",
    "half": "halve",
    "halves": "halve",
    "halving": "halve",
    "find": "search",
    "finds": "search",
    "finding": "search",
}
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "by",
    "each",
    "in",
    "is",
    "it",
    "of",
    "the",
    "this",
    "to",
    "with",
}


def _semantic_tokens(text: str) -> set[str]:
    tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return {
        _SEMANTIC_TOKEN_ALIASES.get(token, token)
        for token in tokens
        if token not in _STOP_WORDS
    }


def _lexical_mechanism_score(reference: str, student_answer: str) -> float:
    reference_tokens = _semantic_tokens(reference)
    answer_tokens = _semantic_tokens(student_answer)
    if not reference_tokens or not answer_tokens:
        return 0.0
    shared = len(reference_tokens & answer_tokens)
    f1_overlap = 2 * shared / (len(reference_tokens) + len(answer_tokens))
    if shared >= 3:
        return max(f1_overlap, min(1.0, 0.45 + 0.12 * shared))
    return f1_overlap


def _semantic_fallback(concept: str, context: str, student_answer: str) -> dict[str, Any]:
    if not student_answer.strip():
        return {"score": 0.0, "feedback": "No answer was provided.", "key_gap": concept}
    reference = f"{concept}. {context}".strip()
    reference_vector, answer_vector = embed_texts([reference, student_answer])
    cosine = sum(left * right for left, right in zip(reference_vector, answer_vector, strict=True))
    # MiniLM paraphrases cluster around 0.5 while related-but-wrong answers
    # commonly sit near 0.35. Calibrate that useful band onto quiz scores.
    embedding_score = max(0.0, min(1.0, (cosine - 0.21) / 0.4))
    lexical_score = _lexical_mechanism_score(reference, student_answer)
    score = max(embedding_score, lexical_score)
    if score >= 0.75:
        feedback = "Strong answer. You captured the central idea and explained it accurately."
        gap = None
    elif score >= 0.5:
        feedback = "Mostly correct. Add the main mechanism or consequence to make the explanation complete."
        gap = concept
    else:
        feedback = "Your answer does not yet match the core idea in the notes. Revisit the underlying mechanism."
        gap = concept
    return {"score": round(score, 4), "feedback": feedback, "key_gap": gap}


def evaluate_answer_llm(concept: str, context: str, student_answer: str) -> dict[str, Any]:
    if settings.groq_api_key and settings.groq_api_key != "your_groq_key_here":
        prompt = (
            "Evaluate conceptual accuracy, not wording. Return strict JSON with score (0 to 1), "
            "feedback (one or two constructive sentences), and key_gap (string or null). "
            f"Concept: {concept}\nReference context: {context}\nStudent answer: {student_answer}"
        )
        try:
            response = call_external_sync(
                lambda: ChatGroq(
                    api_key=settings.groq_api_key,
                    model="llama-3.3-70b-versatile",
                    temperature=0,
                ).invoke(prompt),
                "Groq quiz evaluation",
            )
            parsed = _parse_evaluation(str(response.content))
            if parsed is not None:
                return parsed
        except (TemporaryAPIError, PermanentAPIError) as exc:
            logger.warning("quiz.groq.fallback", error_type=type(exc).__name__)
    return _semantic_fallback(concept, context, student_answer)


def evaluate_answer(state: QuizState) -> dict:
    evaluation = evaluate_answer_llm(
        state.get("current_concept") or "",
        state.get("current_context") or "",
        state.get("user_answer") or "",
    )
    return {
        "answer_score": evaluation["score"],
        "answer_feedback": evaluation["feedback"],
        "key_gap": evaluation["key_gap"],
        "messages": [{"role": "user", "content": state.get("user_answer") or ""}],
    }


def update_session_state(state: QuizState) -> dict:
    concept = state.get("current_concept") or ""
    score = float(state.get("answer_score") or 0.0)
    asked_concepts = list(state["asked_concepts"])
    if concept and concept not in asked_concepts:
        asked_concepts.append(concept)

    results = [
        *state["results"],
        {
            "concept": concept,
            "question": state.get("current_question") or "",
            "answer": state.get("user_answer") or "",
            "score": score,
            "feedback": state.get("answer_feedback") or "",
        },
    ]
    records = {record["concept"]: dict(record) for record in state["weak_concepts"]}
    record = records.get(
        concept,
        ConceptRecord(concept=concept, times_asked=0, times_correct=0, last_score=0.0),
    )
    record["times_asked"] += 1
    record["times_correct"] += int(score >= 0.6)
    record["last_score"] = score
    if score < 0.6:
        records[concept] = record
    else:
        records.pop(concept, None)

    difficulty = "hard" if score >= 0.8 else "medium" if score >= 0.5 else "easy"
    return {
        "questions_asked": state["questions_asked"] + 1,
        "asked_concepts": asked_concepts,
        "results": results,
        "weak_concepts": list(records.values()),
        "current_difficulty": difficulty,
    }


def decide_next_action(state: QuizState) -> dict:
    return {}


def should_continue(state: QuizState) -> str:
    if state["session_complete"] or state["questions_asked"] >= state["max_questions"]:
        return "end_session"
    concept = state.get("current_concept") or ""
    if (state.get("answer_score") or 0.0) < 0.4 and state["times_probed"].get(concept, 0) < 2:
        return "probe_deeper"
    return "next_concept"


def generate_session_summary(state: QuizState) -> dict:
    average = (
        sum(item["score"] for item in state["results"]) / len(state["results"])
        if state["results"]
        else 0.0
    )
    return {
        "session_complete": True,
        "summary": f"Completed {len(state['results'])} questions with an average score of {average:.2f}.",
    }


def build_quiz_graph():
    graph = StateGraph(QuizState)
    graph.add_node("select_concept", select_next_concept)
    graph.add_node("retrieve_context", retrieve_concept_context)
    graph.add_node("generate_question", generate_question)
    graph.add_node("await_answer", await_user_answer)
    graph.add_node("evaluate_answer", evaluate_answer)
    graph.add_node("update_state", update_session_state)
    graph.add_node("decide_next", decide_next_action)
    graph.add_node("generate_summary", generate_session_summary)
    graph.set_entry_point("select_concept")
    graph.add_edge("select_concept", "retrieve_context")
    graph.add_edge("retrieve_context", "generate_question")
    graph.add_edge("generate_question", "await_answer")
    graph.add_edge("await_answer", "evaluate_answer")
    graph.add_edge("evaluate_answer", "update_state")
    graph.add_edge("update_state", "decide_next")
    graph.add_conditional_edges(
        "decide_next",
        should_continue,
        {
            "probe_deeper": "generate_question",
            "next_concept": "select_concept",
            "end_session": "generate_summary",
        },
    )
    graph.add_edge("generate_summary", END)
    return graph.compile(interrupt_after=["await_answer"])
