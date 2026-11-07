#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
state_root="$repo_root/.symphony"
symphony_bin="$repo_root/.tools/symphony/symphony-v0.0.3-linux_x86_64"
codex_bin="$repo_root/.tools/codex/codex-x86_64-unknown-linux-musl"
codex_code_mode_host="$repo_root/.tools/codex/codex-code-mode-host"

for executable in git sed sha256sum; do
  command -v "$executable" >/dev/null || { printf 'Missing command: %s\n' "$executable" >&2; exit 1; }
done
test -f "$symphony_bin" || { printf 'Run scripts/Install-SymphonyRuntime.ps1 first.\n' >&2; exit 1; }
test -f "$codex_bin" || { printf 'Linux Codex binary is missing.\n' >&2; exit 1; }
test -f "$codex_code_mode_host" || { printf 'Run scripts/Install-SymphonyRuntime.ps1 to install the Codex Code Mode host.\n' >&2; exit 1; }
printf '%s  %s\n' 'ea35a04a54a6d37c0cafe3f195da871e47614a8c05765b90dbb4cac32e1435ee' "$symphony_bin" | sha256sum --check --status
chmod u+x "$symphony_bin" "$codex_bin" "$codex_code_mode_host"

# Link to an existing login without copying a credential into the repo.
auth_file="${SYMPHONY_CODEX_AUTH_FILE:-$HOME/.codex/auth.json}"
test -f "$auth_file" || { printf 'Codex login is required.\n' >&2; exit 1; }
mkdir -p "$state_root/codex-home" "$state_root/workspaces" "$state_root/log"
if test -e "$state_root/codex-home/auth.json" && ! test -L "$state_root/codex-home/auth.json"; then
  printf 'An existing private auth file is present; refusing to replace it.\n' >&2
  exit 1
fi
ln -sfn -- "$auth_file" "$state_root/codex-home/auth.json"
export CODEX_HOME="$state_root/codex-home"
export SYMPHONY_SOURCE_REPO="$repo_root"
export SYMPHONY_WORKSPACE_ROOT="$state_root/workspaces"
export SYMPHONY_CODEX_BIN="$codex_bin"

"$codex_bin" --version
"$codex_bin" login status
if ! "$codex_bin" sandbox -- /bin/true; then
  printf 'Codex Linux sandbox check failed. On Ubuntu, install bubblewrap and retry.\n' >&2
  exit 1
fi
if runtime_output="$("$symphony_bin" "$state_root/.preflight-missing-workflow.md" --i-understand-that-this-will-be-running-without-the-usual-guardrails 2>&1)"; then
  printf 'Unexpected Symphony preflight success.\n' >&2
  exit 1
elif [[ "$runtime_output" != *'Workflow file not found:'* ]]; then
  printf '%s\n' "$runtime_output" >&2
  exit 1
fi
printf 'Symphony v0.0.3 runtime loaded; no tracker or agent was started.\n'
printf 'Runtime checks passed. Linear connectivity has not been tested.\n'

if [[ "${1:-check}" == 'check' ]]; then
  exit 0
fi
[[ "${1:-}" == 'start' ]] || { printf 'Use check or start.\n' >&2; exit 1; }
project_slug="${SYMPHONY_LINEAR_PROJECT_SLUG:-}"
[[ "$project_slug" =~ ^[a-zA-Z0-9][a-zA-Z0-9-]{0,159}$ ]] || { printf 'A valid Linear project slugId is required.\n' >&2; exit 1; }
git -C "$repo_root" rev-parse --verify HEAD >/dev/null || { printf 'The source repo needs an initial Git commit before creating issue copies.\n' >&2; exit 1; }
if [[ -z "${LINEAR_API_KEY:-}" && -t 0 ]]; then
  IFS= read -rsp 'Linear Personal API key: ' LINEAR_API_KEY
  printf '\n' >&2
fi
[[ -n "${LINEAR_API_KEY:-}" ]] || { printf 'LINEAR_API_KEY is required.\n' >&2; exit 1; }
export LINEAR_API_KEY

sed "s/__LINEAR_PROJECT_SLUG__/$project_slug/" "$repo_root/WORKFLOW.md" > "$state_root/WORKFLOW.md"
printf 'Symphony dashboard: http://localhost:%s\n' "${SYMPHONY_DASHBOARD_PORT:-4090}"
printf 'Only labeled active issues in the selected project will run. Stop with Ctrl+C.\n'
cd "$repo_root"
exec "$symphony_bin" "$state_root/WORKFLOW.md" \
  --logs-root "$state_root/log" \
  --port "${SYMPHONY_DASHBOARD_PORT:-4090}" \
  --i-understand-that-this-will-be-running-without-the-usual-guardrails
