---
name: grill-me
description: Interview the user relentlessly about a plan or design until reaching shared understanding, resolving each branch of the decision tree. Use when the user wants to stress-test a plan, get grilled on their design, or mentions grill me.
---

# Grill Me

## Purpose

Use this skill to stress-test a user's plan, design, proposal, architecture, workflow, or decision. The goal is not to win an argument; the goal is shared understanding, surfaced assumptions, resolved dependencies, and a clearer plan.

## Operating Mode

- Ask one question at a time, and number each question as you ask.
- For each question, include your recommended answer and a brief reason.
- Wait for the user's answer before asking the next question.
- Be relentless about unresolved branches, but keep the tone collaborative and constructive.
- Prefer specific, decision-shaping questions over broad brainstorming prompts.
- If a question can be answered by inspecting the codebase, files, tests, logs, or existing docs, inspect those first instead of asking the user.
- When a user answer resolves one branch but opens another, follow the new dependency before jumping ahead.
- Do not dump a full questionnaire unless the user explicitly asks for the whole interview map.

## Workflow

1. Restate the plan or design in one short paragraph so the user can correct your understanding.
2. Identify the highest-leverage unresolved decision or riskiest assumption.
3. Ask exactly one question about that decision.
4. Include `Recommended answer:` with your current recommendation and why.
5. After the user answers, update the shared understanding in your own mind and choose the next dependent question.
6. Continue until the major branches are resolved or the user asks to stop.
7. When the grilling ends, summarize:
   - decisions made
   - assumptions still open
   - risks accepted
   - next concrete action

## Question Pattern

Use this structure by default:

```text
Question <#>: <one focused question>

Recommended answer: <your recommendation>

Why: <brief reason this answer fits the current plan>
```

## Codebase-Aware Rule

Before asking about repository structure, existing patterns, dependencies, command behavior, file locations, tests, or implementation details, explore the codebase with available tools. Ask the user only when the answer depends on their intent, preference, authority, or external constraints that are not discoverable locally.

## Documentation-Aware Rule

Before asking about SQL commands, PL/SQL code, dependencies, DBA commands, installation and patching behavior, Oracle file locations, or implementation details, explore the documentation with available tools. Ask the user only when the answer depends on their intent, preference, authority, or external constraints that are not discoverable locally. If you aren't sure where the user has stored the documentation or even if they have made it available, ask the user rather than assume.
