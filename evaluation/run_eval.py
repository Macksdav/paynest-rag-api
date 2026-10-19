"""
Evaluate the RAG pipeline against a small labelled question set.

  LLM_MODE=offline python -m evaluation.run_eval            # fast, no models (used in CI)
  LLM_MODE=ollama  python -m evaluation.run_eval            # real models

Three metrics:
  retrieval hit rate   did the expected fact appear in the retrieved sources?
  answer accuracy      does the answer contain the expected text?
  abstention rate      for questions the knowledge base cannot answer, did the
                       assistant say it cannot find the answer (no hallucination)?

Exits with code 1 if a metric is below its threshold, so CI can block a bad change.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.rag import FALLBACK, build_rag_service

QUESTIONS = Path(__file__).parent / "questions.json"


async def evaluate():
    service = build_rag_service()
    cases = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    rows, hits, correct, abstained = [], 0, 0, 0
    answerable = [c for c in cases if c["expected_fact"]]
    unanswerable = [c for c in cases if not c["expected_fact"]]

    for case in cases:
        result = await service.ask(case["question"])
        if case["expected_fact"]:
            hit = any(case["expected_fact"] in s for s in result["sources"])
            ok = case["answer_contains"].lower() in result["answer"].lower()
            hits += hit
            correct += ok
            rows.append((case["question"], "answerable", "hit" if hit else "MISS", "ok" if ok else "WRONG"))
        else:
            ok = FALLBACK in result["answer"]
            abstained += ok
            rows.append((case["question"], "unanswerable", "-", "abstained" if ok else "HALLUCINATED"))

    return rows, hits / len(answerable), correct / len(answerable), abstained / len(unanswerable), service.mode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-hit-rate", type=float, default=0.8)
    parser.add_argument("--min-answer-accuracy", type=float, default=0.8)
    parser.add_argument("--min-abstention", type=float, default=0.66)
    args = parser.parse_args()

    rows, hit_rate, accuracy, abstention, mode = asyncio.run(evaluate())

    print(f"Mode: {mode}\n")
    print(f"{'Question':<58} {'Type':<13} {'Retrieval':<10} Result")
    print("-" * 95)
    for q, kind, retrieval, result in rows:
        print(f"{q[:56]:<58} {kind:<13} {retrieval:<10} {result}")
    print()
    print(f"Retrieval hit rate : {hit_rate:.0%}  (min {args.min_hit_rate:.0%})")
    print(f"Answer accuracy    : {accuracy:.0%}  (min {args.min_answer_accuracy:.0%})")
    print(f"Abstention rate    : {abstention:.0%}  (min {args.min_abstention:.0%})")

    failed = (hit_rate < args.min_hit_rate or accuracy < args.min_answer_accuracy
              or abstention < args.min_abstention)
    print("\nRESULT:", "FAILED" if failed else "PASSED")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
