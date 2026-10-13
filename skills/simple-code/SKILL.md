---
name: simple-code
description: Refactor existing classes, methods, or recent changes with the smallest useful edits, preserving behavior and the existing architecture. Use when explicitly invoked or when the user requests minimal refactoring, code simplification, or reuse of existing code.
---

# Simple Code

Simplify the requested code with minimal changes. Preserve its behavior and existing architecture, and prefer compatible functions and structures already in the project.

## Scope

- Identify the requested class, method, files, or change set. Refactor only that scope and the directly necessary callers.
- For recent changes, preserve the intended behavior of the current implementation, including the requested feature or fix. Do not revert it to the earlier behavior merely to reduce the diff.
- A request to perform refactoring authorizes the necessary local edits. If the user asks only for analysis or suggestions, provide them without modifying files.

## Refactoring Rules

- Inspect the existing implementation, relevant callers, and nearby reusable functions before editing. Verify that a candidate's input assumptions, outputs, error handling, and side effects match the code being replaced; similar names or shapes are not enough.
- Reuse existing compatible functions and structures rather than duplicating their logic. Keep changes within the current responsibility and dependency boundaries; do not introduce a cross-layer dependency just to reuse code.
- Prefer local simplifications such as removing redundant branches, consolidating equivalent calculations, or replacing duplicated logic with an existing helper. Remove code only after confirming it has no required behavior or side effects.
- Preserve observable results, public contracts, validation, exception handling, side-effect order, and relevant state, threading, lifecycle, and cancellation behavior. Do not treat fewer lines as proof of equivalence.
- Follow existing architecture and code conventions. Do not introduce layers, modules, interfaces, generic frameworks, design patterns, or architecture migrations to satisfy an abstract ideal such as Clean Architecture or SOLID.
- Avoid unrelated renaming, moving files, formatting churn, dependency changes, and broad cleanup. A small local helper is acceptable only when existing code cannot be reused and it clearly reduces duplication without broadening the change.
- If no useful simplification preserves behavior within this scope, leave the code unchanged and explain why. Report a discovered bug separately rather than silently mixing a behavior fix into refactoring.

## Verification and Result

Compare the original and revised behavior on the paths affected by the refactoring, including relevant failure cases and side effects. Use focused existing checks where practical; add a regression check only when needed to establish a meaningful invariant. Do not weaken tests to make the change pass, and do not claim runtime equivalence from compilation alone.

Respond in Korean. Briefly report what was simplified, what existing code was reused, verification performed, and any remaining uncertainty. Keep suggested broader changes separate from the implemented refactoring.
