# 0001. The LLM explains pre-computed aggregates and never sees raw data

**Status:** Accepted

## Context

Sellers want a short "what happened this week" text. An LLM writes that well, but it is unreliable at arithmetic, and sending orders to a third-party API would leak personal data (LGPD).

## Decision

All numbers are computed in SQL. `weekly_facts()` builds a small JSON (KPIs for this week and last week, top five products, ABC counts, alert names) and that JSON is the only input to Claude. The system prompt tells the model to use only those facts. The feature is off unless `ANTHROPIC_API_KEY` is set.

## Consequences

- No hallucinated numbers: every figure in the text exists in the input.
- No personal data leaves the system; `test_summary_sends_only_aggregates` guards it.
- The text can only be as rich as the facts. New insights mean adding facts, not prompting harder.
- The rest of the app works the same without the model.
