#!/usr/bin/env bash
# ==============================================================================
# TARClub updater: bot settings only. The update logic is bot_update_lib.sh, vendored from
# tarbot-console (the updater-drift CI job fails if the copy differs from the pinned commit).
# ==============================================================================

BOT_NAME="TARClub"
BOT_ENV_PREFIX="TARCLUB"
BOT_REPO_SLUG="TARBot-OSS/TARClub-bot"
BOT_LEGACY_REMOTE_RE="Dellrall/(TARClub-bot|TARClub)"
BOT_DEFAULT_DB="tarclub.db"
BOT_DEFAULT_SERVICE="tarclub"
BOT_START_ARGS="-m tarclub"
BOT_COMPILE_TARGETS="tarclub"
BOT_BACKUP_PREFIX="tarclub"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=bot_update_lib.sh
source "${SCRIPT_DIR}/bot_update_lib.sh"
bot_update_main "$@"
