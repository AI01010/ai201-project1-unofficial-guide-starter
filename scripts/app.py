"""Gradio web UI for the Unofficial Guide RAG system.

Run with: python scripts/app.py
Then open http://localhost:7860

Three text fields:
  - Question (input)
  - Answer (output, model response)
  - Retrieved from (output, source files + URLs the retrieval surfaced)

Also shows top-1 chunk distance so you can eyeball retrieval quality.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gradio as gr

from query import ask, EVAL_QUERIES


def _format_sources(sources: list[dict]) -> str:
    if not sources:
        return "(no sources — system declined to answer)"
    lines = []
    for s in sources:
        url = s.get("url") or ""
        excerpts = ", ".join(f"#{c}" for c in s["chunks_used"])
        lines.append(f"• {s['filename']}  ({excerpts})\n   {url}" if url else f"• {s['filename']}  ({excerpts})")
    return "\n".join(lines)


def _format_retrieval_debug(retrieved) -> str:
    if not retrieved:
        return "(no chunks retrieved)"
    lines = []
    for i, h in enumerate(retrieved, 1):
        course = h.metadata.get("course", "?")
        prof = h.metadata.get("prof_name", "")
        prof_part = f" prof={prof}" if prof else ""
        lines.append(f"[{i}] distance={h.distance:.3f}  course={course}{prof_part}")
    return "\n".join(lines)


def handle_query(question: str):
    """Gradio callback. Returns (answer_text, sources_text, retrieval_debug_text)."""
    question = (question or "").strip()
    if not question:
        return "Ask me something.", "", ""
    a = ask(question)
    return a.answer, _format_sources(a.sources), _format_retrieval_debug(a.retrieved)


with gr.Blocks(title="The Unofficial Guide — UTD CS") as demo:
    gr.Markdown(
        "# The Unofficial Guide\n"
        "RAG over UT Dallas CS class and professor reviews from RMP, "
        "r/utdallas, and the official UTD catalog. Ask about courses by code "
        "(`CS 3345`, `CS 4337`, etc.) or by topic (`data structures`, "
        "`machine learning`)."
    )
    with gr.Row():
        with gr.Column(scale=2):
            question = gr.Textbox(
                label="Your question",
                placeholder="e.g. What do students say about Omar Hamdy's exams in CS 3345?",
                lines=2,
            )
            ask_btn = gr.Button("Ask", variant="primary")
            gr.Examples(
                examples=[[q] for _, q in EVAL_QUERIES],
                inputs=question,
                label="Try one of the eval-plan questions",
            )
        with gr.Column(scale=3):
            answer = gr.Textbox(label="Answer", lines=10, show_copy_button=True)
            sources = gr.Textbox(label="Retrieved from", lines=4)
            with gr.Accordion("Retrieval debug (distances + courses)", open=False):
                retrieval_debug = gr.Textbox(label="", lines=6, show_label=False)

    ask_btn.click(handle_query, inputs=question, outputs=[answer, sources, retrieval_debug])
    question.submit(handle_query, inputs=question, outputs=[answer, sources, retrieval_debug])


if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=7860, inbrowser=False)
