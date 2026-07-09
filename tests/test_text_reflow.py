"""Tests for PDF markdown reflow."""

from __future__ import annotations

from pathlib import Path

from knotica.core.page import parse_page
from knotica.core.text_reflow import reflow_pdf_markdown

S1_SAMPLE = """\
## 1 Introduction
The past two years have witnessed the overwhelming evolution of increasingly capable large language models
(LLMs) into powerful AI agents (Matarazzo and Torlone, 2025; Minaee et al., 2025; Luo et al., 2025a).
These foundation-model-powered agents have demonstrated remarkable progress across diverse domains
such as deep research (Xu and Peng, 2025; Zhang et al., 2025p), software engineering (Wang et al., 2024i),
and scientific discovery (Wei et al., 2025c), continuously advancing the trajectory toward artificial general
interlligence (AGI) (Fang et al., 2025a; Durante et al., 2024). Although early conceptions of “agents” were
highly heterogeneous, a growing consensus has since emerged within the community: beyond a pure LLM
backbone, an agent is typically equipped with capabilities such as reasoning, planning, perception, memory, and
tool-use.

Among these agentic faculties, memory stands out as a cornerstone, explicitly enabling the transformation
of static LLMs, whose parameters cannot be rapidly updated, into adaptive agents capable of continual
adaptation through environmental interaction (Zhang et al., 2025s; Wu et al., 2025g). From an application
perspective, numerous domains demand agents with proactive memory management rather than ephemeral,
forgetful behaviors: personalized chatbots (Chhikara et al., 2025; Li et al., 2025b), recommender systems (Liu
et al., 2025c), social simulations (Park et al., 2023; Yang et al., 2025), and financial investigations (Zhang
et al., 2024) all rely on the agent’s ability to process, store, and manage historical information.
"""


def test_reflow_joins_mid_sentence_pdf_wraps() -> None:
    reflowed = reflow_pdf_markdown(S1_SAMPLE)
    assert "language models (LLMs) into powerful" in reflowed
    assert "language models\n(LLMs)" not in reflowed
    assert "general\ninterlligence" not in reflowed
    assert "general interlligence" in reflowed
    assert "Although early conceptions" in reflowed.split("interlligence (AGI)")[1]


def test_reflow_preserves_paragraph_breaks_after_sentences() -> None:
    reflowed = reflow_pdf_markdown(S1_SAMPLE)
    assert "\n\nAmong these agentic faculties" in reflowed


def test_reflow_joins_wrapped_circled_bullet_items() -> None:
    sample = """\
Key Questions
 ❶ How is agent memory defined, and how does it relate to related concepts such as LLM memory,
retrieval-augmented generation (RAG), and context engineering?
 ❷ Forms: What architectural forms can agent memory take?
"""
    reflowed = reflow_pdf_markdown(sample)
    assert "LLM memory, retrieval-augmented generation" in reflowed
    assert "\n\n❷ Forms:" in reflowed or "\n\n ❷ Forms:" in reflowed


def test_reflow_structures_subheadings_and_question_blocks() -> None:
    sample = """\
## 1 Introduction
Agent Memory Needs A New Taxonomy Given the growing significance of agent memory.
The motivation is twofold: ❶ First point ends here. ❷ Second point ends here. Therefore, we propose a framework.
Key Questions
❶ How is memory defined?
To address question ❶, we define terms. Question ❷ examines forms. Question ❸ concerns functions.
Contributions The contributions of this survey are as follows.
"""
    reflowed = reflow_pdf_markdown(sample)
    assert "### Agent Memory Needs A New Taxonomy" in reflowed
    assert "\n\nGiven the growing significance" in reflowed
    assert "\n\n❷ Second point" in reflowed
    assert "\n\nTherefore, we propose" in reflowed
    assert "### Key Questions" in reflowed
    assert "\n\nQuestion ❷ examines" in reflowed
    assert "### Contributions" in reflowed


def test_reflow_preserves_markdown_headers() -> None:
    reflowed = reflow_pdf_markdown(S1_SAMPLE)
    assert reflowed.startswith("## 1 Introduction\n")


def test_hu2025memory_s1_intro_reflow_is_idempotent() -> None:
    path = Path("/Users/fperez/dev/data/knotica/sources/agentic-systems/hu2025memory-s1-intro.md")
    if not path.exists():
        return
    _frontmatter, _error, body = parse_page(path.read_text(encoding="utf-8"))
    assert _error is None
    reflowed = reflow_pdf_markdown(body)
    assert reflow_pdf_markdown(reflowed) == reflowed
    assert "language models (LLMs) into powerful" in reflowed
    assert "language models\n(LLMs)" not in reflowed
