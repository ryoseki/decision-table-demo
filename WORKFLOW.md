---
# Keep the prompt body ASCII for the Symphony v0.0.3 workflow parser.
tracker:
  kind: linear
  provider:
    api_key: $LINEAR_API_KEY
    project_slug: __LINEAR_PROJECT_SLUG__
  required_labels:
    - symphony-pilot
  active_states:
    - Todo
    - In Progress
  terminal_states:
    - Done
    - Canceled
    - Cancelled
    - Duplicate
polling:
  interval_ms: 10000
workspace:
  root: $SYMPHONY_WORKSPACE_ROOT
hooks:
  after_create: |
    git clone --no-hardlinks "$SYMPHONY_SOURCE_REPO" .
agent:
  max_concurrent_agents: 1
  max_turns: 5
codex:
  command: '"$SYMPHONY_CODEX_BIN" app-server'
  approval_policy: never
server:
  host: 127.0.0.1
---

You are working on Linear issue {{ issue.identifier }} for the fraud review demo described in AGENTS.md.

Title: {{ issue.title }}
Current state: {{ issue.state }}
URL: {{ issue.url }}
Description:
{{ issue.description }}

{% if attempt %}
This is attempt {{ attempt }}. Check the existing workspace and progress before continuing.
{% endif %}

## Start work

Read AGENTS.md and the documents relevant to the issue. Use `linear_graphql` to check the issue state, the team's available states, and the existing comments. Move the issue from `Todo` to `In Progress`. If it is already `In Progress`, continue from the recorded progress. If it is outside the active states, stop.

Update the existing "Symphony workpad" comment, or create one if there is none. Record the plan, changed files, checks, and decisions that need human input. Write the workpad and reports in Japanese. Use repository-relative paths for files and the issue identifier for its workspace. Omit absolute paths and user-specific machine details from chat, documents, workpads, and other reports, including quoted logs and links. Limit Linear mutations to progress and state updates on the assigned issue.

## Check the acceptance criteria before editing

Before any file edit, check that the acceptance criteria say, for each item, which layer confirms it (API contract, frontend unit, E2E, or infrastructure wiring) and how (a command or a manual check). Also check that the implicit prerequisites are stated, for example "starts with docker compose up" and "the standard test commands pass". See docs/testing-policy.md.

If a layer or a prerequisite is missing, do not start implementing and do not edit the issue description. Record the gap and a proposed wording for the missing criteria in the workpad, then move the issue to `In Review` and wait for the reviewer's answer. Do not wait for input inside the run.

## Consult official documentation before editing

Follow the OpenAI documentation rule in the workspace's AGENTS.md. Before creating or editing files, search for and read the relevant official documentation. Report the source, date, guidance, and how it applies in the chat and the workpad. At handoff, record the sources, changes, checks performed, and checks still pending in both places.

If that section is missing or you cannot verify the official documentation, keep file operations read-only. Report what you could not verify and follow the review handoff procedure. If Linear is unavailable, report which updates are pending.

## Work within the issue

Follow the issue's deliverables and acceptance criteria. In docs/requirements.md, distinguish approved requirements, design proposals, and open questions. Keep requirements, planning, and implementation within the assigned scope.

Leave deliverables in the issue workspace. If a local Git checkpoint is appropriate, include only the relevant documents and use the configured Git identity. If a checkpoint cannot be created, record the reason and list the changed and untracked files in the workpad.

## Verify by layer

Follow docs/testing-policy.md. Confirm the layers in this order, and do not move to the next layer while the previous one fails: API contract (no database), frontend unit (API mocked), E2E (one end-to-end flow), infrastructure wiring. Database integration tests cover only behavior that also holds on Aurora DSQL; do not test PostgreSQL-specific behavior. Run the dependency-direction checks too (lint-imports for the backend, eslint for the frontend; see ARCHITECTURE.md). Follow the remediation text in the error message. Do not loosen or bypass a check without asking a human. Never report an unrun check as passed.

Map each acceptance item to the test (layer and test name) that confirms it, and list the map in the workpad and in the final report, together with the layers you confirmed and the layers still unconfirmed. An item with no test is unconfirmed. For UI work, test components as well as pure logic. A behavior checked in E2E must also be checked in a lower layer, except behavior that needs a real browser or real services. After adding a new check (ESLint rule or import-linter contract), always introduce a violation temporarily and confirm it fails. After writing a new test, see it fail first or break the code temporarily and confirm it fails; take extra care with negative assertions. For implementation tasks, before the final hand-off, write the feature details (the Linear issue title, description, and acceptance criteria, copied as written; no work notes, no test results, no verification comments) to a temporary file outside the repository, then run `bash scripts/review-tests.sh --feature-file <that file>`. It starts a separate fresh read-only Claude Code session that is given the feature details and the implementation (the list of changed files), not your test results or self-assessment, and that reviews missing frontend, backend, and E2E tests from a senior engineer's view (see docs/test-review-prompt.md). Fix the findings that are in scope, report the rest, and record in the final report what you fixed and what you did not. If the claude CLI is unavailable, report that this review was not run.

## Hand off for review

Record the deliverables, observed results, and remaining questions in the same workpad. Then move the issue to `In Review`, which is outside the active states. If changes are needed, the reviewer records them and returns the issue to `Todo`. A human reviews the result before it moves to `Done`.

Do not move the issue to `In Review` for final review while a layer that you can run yourself is still unconfirmed; keep working on it. A layer that needs a human action or credentials you do not have (for example, real Aurora DSQL) is the exception: state it as unconfirmed and hand off. If required credentials, tools, or permissions are unavailable, record the last successful step and what is needed to resume, then move the issue to `In Review`. If Linear is unavailable, report which workpad or state updates are pending.
