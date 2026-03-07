#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${RELEASE_ENV_FILE:-$ROOT_DIR/.env.release}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE"
  echo "Create it from $ROOT_DIR/.env.release.example"
  exit 1
fi

set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

RELEASE_SSH_AUTH_MODE="${RELEASE_SSH_AUTH_MODE:-key}"
RELEASE_1PASSWORD_AGENT_SOCK="${RELEASE_1PASSWORD_AGENT_SOCK:-$HOME/.1password/agent.sock}"

required_vars=(
  RELEASE_SSH_HOST
  RELEASE_SSH_USER
  RELEASE_REMOTE_DIR
  RELEASE_PM2_APP
)

for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "Missing required variable in $ENV_FILE: $var_name"
    exit 1
  fi
done

SSH_TARGET="${RELEASE_SSH_USER}@${RELEASE_SSH_HOST}"
REMOTE_CMD="cd \"${RELEASE_REMOTE_DIR}\" && git pull && pm2 restart \"${RELEASE_PM2_APP}\""

echo "Releasing to ${SSH_TARGET}:${RELEASE_REMOTE_DIR}"
case "$RELEASE_SSH_AUTH_MODE" in
  key)
    if [[ -z "${RELEASE_SSH_KEY_PATH:-}" ]]; then
      echo "Missing required variable in $ENV_FILE: RELEASE_SSH_KEY_PATH (mode=key)"
      exit 1
    fi
    if [[ ! -f "$RELEASE_SSH_KEY_PATH" ]]; then
      echo "SSH key not found at: $RELEASE_SSH_KEY_PATH"
      exit 1
    fi
    ssh -i "$RELEASE_SSH_KEY_PATH" "$SSH_TARGET" "$REMOTE_CMD"
    ;;
  onepassword)
    if [[ -z "${SSH_AUTH_SOCK:-}" ]]; then
      if [[ ! -S "$RELEASE_1PASSWORD_AGENT_SOCK" ]]; then
        echo "1Password agent socket not found: $RELEASE_1PASSWORD_AGENT_SOCK"
        exit 1
      fi
      export SSH_AUTH_SOCK="$RELEASE_1PASSWORD_AGENT_SOCK"
    fi
    ssh "$SSH_TARGET" "$REMOTE_CMD"
    ;;
  *)
    echo "Invalid RELEASE_SSH_AUTH_MODE: $RELEASE_SSH_AUTH_MODE (expected: key or onepassword)"
    exit 1
    ;;
esac
