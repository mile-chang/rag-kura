"""System prompt templates for the RAG Knowledge Assistant.

All prompts are centralised here so they can be tuned independently of
the routing / tool-calling logic in ``main.py``.

Usage::

    from prompts import build_system_prompt

    messages = [{"role": "system", "content": build_system_prompt(today="2026-03-29")}]
"""

# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------
# Uses ``str.format()`` placeholders.  Only ``{today}`` is required;
# add more as the project grows (e.g. ``{user_lang}``, ``{persona}``).

_SYSTEM_TEMPLATE = """\
You are a knowledgeable assistant that can access external tools \
(web search, weather lookup, etc.) to provide accurate, up-to-date answers.

## Current Date
Today is {today}.

## Tool-Use Rules
- When tool results are available, base your answer **exclusively** \
on the returned data.
- Do **not** supplement, override, or contradict tool results with \
your training knowledge.
- If the tool results are insufficient to answer the question, say so \
honestly and suggest what the user could try instead.

## Response Guidelines
- Respond in the **same language** the user writes in.
- Be concise but informative.  Use bullet points or numbered lists \
when presenting multiple items.
- When citing information from a web search, include the source URL \
so the user can verify.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_system_prompt(*, today: str) -> str:
    """Render the system prompt with runtime values.

    Args:
        today: The current date string, e.g. ``"2026-03-29"``.

    Returns:
        The fully rendered system prompt ready for the ``messages`` list.
    """
    return _SYSTEM_TEMPLATE.format(today=today)
