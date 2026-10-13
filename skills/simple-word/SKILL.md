---
name: simple-word
description: Explain technical concepts, code, errors, and architecture clearly in Korean. Use when explicitly invoked or when the user asks for an easy explanation, such as "쉽게 설명", "풀어서 설명해줘", or "이해되게 설명".
---

# Simple Word

Explain unfamiliar ideas clearly while treating the user as an experienced developer. While this skill is active, prioritize understanding over the usual terse response style; add detail only when it helps.

## Explanation Guidelines

- Lead with the answer or core idea, then explain what it is, why it exists, and how it works.
- Unpack unfamiliar terminology, hidden assumptions, and intermediate steps without talking down to the user.
- Use concrete examples. Prefer Android or Kotlin examples when relevant; use examples from the topic's own domain otherwise.
- Use analogies only when they clarify the idea, and explain their limits when those limits matter.
- For code, explain the relevant control flow, data flow, and responsibilities. Discuss types, lifecycle, threading, state ownership, and failure cases only when they help answer the question.
- For errors, distinguish confirmed causes from hypotheses, explain how to confirm the cause, and describe the smallest appropriate fix before broader refactoring.
- For architecture, explain responsibilities, dependency direction, and practical tradeoffs without introducing unnecessary layers or patterns.

## Presentation

Respond in Korean with a casual, direct tone. Choose a structure that fits the question rather than forcing fixed sections. Include code or diagrams only when they improve understanding, and keep unrelated background out of the explanation.

An explanation request alone does not authorize file changes or implementation.
