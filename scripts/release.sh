#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${RELEASE_CONFIG_FILE:-$ROOT_DIR/release.json}"

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required but not installed."
  exit 1
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Missing config file: $CONFIG_FILE"
  echo "Create it from $ROOT_DIR/release.json.example"
  exit 1
fi

usage() {
  echo "Usage: $0 [host-name ...]"
  echo "  No args releases to all hosts in $CONFIG_FILE."
  echo "  Pass one or more host names to release to a subset."
  echo ""
  echo "Available hosts:"
  jq -r '.hosts[].name' "$CONFIG_FILE" | sed 's/^/  - /'
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

all_names=$(jq -r '.hosts[].name' "$CONFIG_FILE")

if [[ $# -eq 0 ]]; then
  selected_names="$all_names"
else
  selected_names=""
  for name in "$@"; do
    if ! grep -qx "$name" <<<"$all_names"; then
      echo "Unknown host: $name"
      usage
      exit 1
    fi
    selected_names+="$name"$'\n'
  done
fi

expand_path() {
  local p="$1"
  [[ "$p" == "~"* ]] && p="${HOME}${p:1}"
  printf '%s' "$p"
}

release_host() {
  local name="$1"
  local entry
  entry=$(jq -c --arg name "$name" '.hosts[] | select(.name == $name)' "$CONFIG_FILE")

  local ssh_host ssh_user remote_dir pm2_app auth_mode key_path agent_sock
  ssh_host=$(jq -r '.ssh_host // empty' <<<"$entry")
  ssh_user=$(jq -r '.ssh_user // empty' <<<"$entry")
  remote_dir=$(jq -r '.remote_dir // empty' <<<"$entry")
  pm2_app=$(jq -r '.pm2_app // empty' <<<"$entry")
  auth_mode=$(jq -r '.ssh_auth_mode // "key"' <<<"$entry")
  key_path=$(jq -r '.ssh_key_path // empty' <<<"$entry")
  agent_sock=$(jq -r '.onepassword_agent_sock // empty' <<<"$entry")

  for field in ssh_host ssh_user remote_dir pm2_app; do
    if [[ -z "${!field}" ]]; then
      echo "[$name] missing required field: $field"
      return 1
    fi
  done

  local ssh_target="${ssh_user}@${ssh_host}"
  local remote_cmd="cd \"${remote_dir}\" && git pull && pm2 restart \"${pm2_app}\""

  echo "==> [$name] Releasing to ${ssh_target}:${remote_dir}"
  case "$auth_mode" in
    key)
      if [[ -z "$key_path" ]]; then
        echo "[$name] ssh_key_path required for ssh_auth_mode=key"
        return 1
      fi
      key_path=$(expand_path "$key_path")
      if [[ ! -f "$key_path" ]]; then
        echo "[$name] SSH key not found at: $key_path"
        return 1
      fi
      ssh -i "$key_path" "$ssh_target" "$remote_cmd"
      ;;
    onepassword)
      if [[ -z "${SSH_AUTH_SOCK:-}" ]]; then
        local sock
        sock=$(expand_path "${agent_sock:-$HOME/.1password/agent.sock}")
        if [[ ! -S "$sock" ]]; then
          echo "[$name] 1Password agent socket not found: $sock"
          return 1
        fi
        export SSH_AUTH_SOCK="$sock"
      fi
      ssh "$ssh_target" "$remote_cmd"
      ;;
    *)
      echo "[$name] Invalid ssh_auth_mode: $auth_mode (expected: key or onepassword)"
      return 1
      ;;
  esac
}

failed=()
while IFS= read -r name; do
  [[ -z "$name" ]] && continue
  if ! release_host "$name"; then
    failed+=("$name")
  fi
done <<<"$selected_names"

if (( ${#failed[@]} > 0 )); then
  echo ""
  echo "Failed hosts: ${failed[*]}"
  exit 1
fi
