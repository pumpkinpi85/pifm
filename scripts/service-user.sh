#!/usr/bin/env bash
# Resolve the account that should own a piFM install.
# Source this file, then call pifm_resolve_service_user.
# PIFM_USER is an explicit override. Otherwise the installing user is
# SUDO_USER, then USER, when that account is not root.
# Never defaults to a user named pi. Current Raspberry Pi OS does not
# create that account.

pifm_resolve_service_user() {
  local user="${PIFM_USER:-}"
  if [[ -z "$user" ]]; then
    if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
      user="${SUDO_USER}"
    elif [[ -n "${USER:-}" && "${USER}" != "root" ]]; then
      user="${USER}"
    fi
  fi
  if [[ -z "$user" ]]; then
    echo "Could not determine the installing user. Run the installer with sudo from a normal account, or set PIFM_USER to an existing user. A user named pi is not required." >&2
    return 1
  fi
  if ! id "$user" >/dev/null 2>&1; then
    echo "Service user '${user}' does not exist. Create that account or set PIFM_USER to an existing user. A user named pi is not required." >&2
    return 1
  fi
  PIFM_SERVICE_USER="$user"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  set -euo pipefail
  pifm_resolve_service_user
  printf '%s\n' "$PIFM_SERVICE_USER"
fi
