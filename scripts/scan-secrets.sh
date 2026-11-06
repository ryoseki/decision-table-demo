#!/usr/bin/env bash
set -euo pipefail

scope="${1:-worktree}"
case "$scope" in
  worktree|staged|history) ;;
  *) printf 'Usage: bash scripts/scan-secrets.sh [worktree|staged|history]\n' >&2; exit 2 ;;
esac

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repo_root"
version=1.10.0
install_dir="$repo_root/.tools/betterleaks/$version"
binary="$install_dir/betterleaks"

if [[ ! -x "$binary" ]]; then
  case "$(uname -sm)" in
    'Linux x86_64')
      architecture=x64
      checksum=9d9ad503cd5d80d18a939352266c3fcca52f10da9b83cd5cfe646989b13940fe
      ;;
    'Linux aarch64')
      architecture=arm64
      checksum=3d7585cbe4f947aef6e31662f65beb5b43b104381a691b6b81b157ebfbb0c02d
      ;;
    *) printf 'Betterleaks setup requires Linux x86_64 or aarch64.\n' >&2; exit 1 ;;
  esac
  archive_name="betterleaks_${version}_linux_${architecture}.tar.gz"
  mkdir -p -- "$install_dir"
  download_dir="$(mktemp -d "$install_dir/.install.XXXXXX")"
  trap 'rm -f -- "$download_dir/$archive_name" "$download_dir/betterleaks"; rmdir -- "$download_dir"' EXIT
  archive="$download_dir/$archive_name"
  curl --fail --silent --show-error --location \
    "https://github.com/betterleaks/betterleaks/releases/download/v$version/$archive_name" -o "$archive"
  printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check --status
  tar -xzf "$archive" -C "$download_dir" betterleaks
  chmod u+x "$download_dir/betterleaks"
  mv -fT -- "$download_dir/betterleaks" "$binary"
  printf 'Installed verified Betterleaks v%s (%s).\n' "$version" "$architecture"
fi

scan_options=(--regex-engine=stdlib --redact=100 --validation=false --no-banner --no-color --exit-code=10)
case "$scope" in
  staged) "$binary" git --staged "${scan_options[@]}" -- "$repo_root" ;;
  history) "$binary" git "${scan_options[@]}" -- "$repo_root" ;;
  worktree)
    git --no-optional-locks ls-files --cached --others --exclude-standard --deduplicate -z -- \
      ':!.git' ':!.tools' ':!.symphony' |
      {
        files=()
        while IFS= read -r -d '' file; do
          if [[ -f "$file" ]] && IFS= read -r -d '' resolved < <(realpath -ez -- "$file") &&
            [[ "$resolved" == "$repo_root/$file" ]]; then
            files+=("$file")
          fi
        done
        if ((${#files[@]} == 0)); then
          printf 'No files to scan.\n'
        else
          printf 'Scanning %s worktree files with Betterleaks.\n' "${#files[@]}"
          "$binary" dir "${scan_options[@]}" -- "${files[@]}"
        fi
      }
    ;;
esac
