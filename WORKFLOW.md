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

## Consult official documentation before editing

Follow the OpenAI documentation rule in the workspace's AGENTS.md. Before creating or editing files, search for and read the relevant official documentation. Report the source, date, guidance, and how it applies in the chat and the workpad. At handoff, record the sources, changes, checks performed, and checks still pending in both places.

If that section is missing or you cannot verify the official documentation, keep file operations read-only. Report what you could not verify and follow the review handoff procedure. If Linear is unavailable, report which updates are pending.

## Work within the issue

Follow the issue's deliverables and acceptance criteria. In docs/requirements.md, distinguish approved requirements, design proposals, and open questions. Keep requirements, planning, and implementation within the assigned scope.

Leave deliverables in the issue workspace. If a local Git checkpoint is appropriate, include only the relevant documents and use the configured Git identity. If a checkpoint cannot be created, record the reason and list the changed and untracked files in the workpad.

## Hand off for review

Record the deliverables, observed results, and remaining questions in the same workpad. Then move the issue to `In Review`, which is outside the active states. If changes are needed, the reviewer records them and returns the issue to `Todo`. A human reviews the result before it moves to `Done`.

If required credentials, tools, or permissions are unavailable, record the last successful step and what is needed to resume, then move the issue to `In Review`. If Linear is unavailable, report which workpad or state updates are pending.
