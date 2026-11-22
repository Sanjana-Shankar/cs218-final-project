"""Try the chatbot on your computer, using Sanjana's real analysis output.

Run from the repository's top folder:
    python3 -m backend.functions.chatbot.local_chat
    python3 -m backend.functions.chatbot.local_chat --explain    # also test AI explanations

Uses analysis_output.json (made by process_billing_file/cli.py). Needs AWS credentials
with Bedrock access. Type 'quit' to exit.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .chat import answer_question

ROOT = Path(__file__).resolve().parents[3]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", default=str(ROOT / "analysis_output.json"))
    parser.add_argument("--explain", action="store_true", help="Generate aiExplanation for each recommendation first")
    args = parser.parse_args()

    analysis = json.loads(Path(args.analysis).read_text())

    if args.explain:
        from backend.functions.process_billing_file.explanations import add_explanations

        add_explanations(analysis)
        for rec in analysis["recommendations"]:
            print(f"--- {rec['resourceId']} ({rec['category']}) ---\n{rec['aiExplanation']}\n")

    history, conversation_id = [], None
    print("Cost assistant ready. Type a question, or 'quit' to exit.\n")
    while True:
        question = input("You: ").strip()
        if question.lower() in ("quit", "exit"):
            break
        if not question:
            continue
        try:
            response = answer_question(question, analysis, history=history, conversation_id=conversation_id)
        except ValueError as exc:
            print(f"\n[rejected] {exc}\n")
            continue
        conversation_id = response["conversationId"]
        history.append({"question": question, "answer": response["answer"]})
        print(f"\nAssistant (confidence {response['confidence']}): {response['answer']}")
        print(f"Sources: {[s['label'] for s in response['sources']]}\n")


if __name__ == "__main__":
    main()
