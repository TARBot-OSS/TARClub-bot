#!/usr/bin/env bash
# ==============================================================================
# Shared updater for the TAR bots: pre-update DB snapshot, pull, dependencies,
# tests, restart, with rollback on failure. Vendored into each bot as
# scripts/bot_update_lib.sh and sourced by that bot's scripts/update.sh.
#
# Canonical copy: tarbot-console/updater/bot_update_lib.sh. The bot's CI fails if its
# vendored copy differs from the copy at the tarbot-console commit it pins.
#
# The caller sets these before sourcing this file and calling bot_update_main "$@":
#   BOT_NAME              display name, e.g. TARVeri
#   BOT_ENV_PREFIX        prefix of the bot's .env settings, e.g. TARVERI
#   BOT_REPO_SLUG         GitHub org/repo, e.g. TARBot-OSS/TARVeri-bot
#   BOT_DEFAULT_DB        database file, relative to the project root
#   BOT_DEFAULT_SERVICE   systemd unit name
#   BOT_START_ARGS        arguments after the interpreter, e.g. "tarveri_bot.py"
#   BOT_COMPILE_TARGETS   paths for the compile-only check when pytest is missing
#   BOT_BACKUP_PREFIX     file prefix for pre-update snapshots
# Optional:
#   BOT_LEGACY_REMOTE_RE  regex of old origin URLs that are migrated to BOT_REPO_SLUG
#
# Settings are read from the environment or the project .env as ${BOT_ENV_PREFIX}_<NAME>.
# ==============================================================================

set -eo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Read ${BOT_ENV_PREFIX}_<name>, falling back to the default.
bot_setting() {
    local name="${BOT_ENV_PREFIX}_$1"
    echo "${!name:-$2}"
}

load_dotenv() {
    local env_file="$1" line key val
    [ -f "${env_file}" ] || return 0
    while IFS= read -r line || [ -n "$line" ]; do
        # Trim leading whitespace
        line="${line#"${line%%[![:space:]]*}"}"
        # Skip empty lines and comments
        [[ -z "$line" || "$line" =~ ^# ]] && continue
        if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=[[:space:]]*(.*)$ ]]; then
            key="${BASH_REMATCH[1]}"
            val="${BASH_REMATCH[2]}"
            # Strip surrounding quotes if present, along with trailing comments
            if [[ "$val" =~ ^\"(.*)\"[[:space:]]*(#.*)?$ ]]; then
                val="${BASH_REMATCH[1]}"
            elif [[ "$val" =~ ^\x27(.*)\x27[[:space:]]*(#.*)?$ ]]; then
                val="${BASH_REMATCH[1]}"
            else
                val="${val%%#*}"
                val="${val%"${val##*[![:space:]]}"}"
            fi
            export "$key=$val"
        fi
    done < "${env_file}"
}

# Restart the bot in a tmux pane. Ctrl+C stops it (the console shuts down gracefully on an empty
# prompt), then the pane's shell is started again with START_CMD. Never starts a second copy.
restart_in_tmux() {
    local target="$1"
    local waited=0

    if ! command -v tmux >/dev/null 2>&1; then
        log_warn "${BOT_ENV_PREFIX}_TMUX_TARGET is set but tmux is not installed."
        return 1
    fi
    if ! tmux display-message -p -t "${target}" '#{pane_id}' >/dev/null 2>&1; then
        log_warn "tmux pane '${target}' was not found."
        return 1
    fi

    if ! is_pane_at_shell "${target}"; then
        log_info "Stopping the bot in tmux pane '${target}'..."
        tmux send-keys -t "${target}" C-c
        while ! is_pane_at_shell "${target}"; do
            sleep 1
            waited=$((waited + 1))
            if [ "${waited}" -eq 10 ]; then
                log_warn "Bot still running after 10s. Sending Ctrl+C again."
                tmux send-keys -t "${target}" C-c
            fi
            if [ "${waited}" -ge 60 ]; then
                log_error "Bot did not stop within 60s. Not starting a second copy; restart it manually."
                return 1
            fi
        done
        log_success "Bot stopped."
    fi

    log_info "Starting the bot in tmux pane '${target}'..."
    tmux send-keys -t "${target}" "cd ${ROOT_Q} && ${START_CMD}" Enter
    sleep 5
    if is_pane_at_shell "${target}"; then
        log_error "Bot exited right after start. Last output:"
        tmux capture-pane -p -t "${target}" | tail -n 20
        return 1
    fi
    log_success "Bot started in tmux pane '${target}'."
}

is_pane_at_shell() {
    local cmd
    cmd="$(tmux display-message -p -t "$1" '#{pane_current_command}' 2>/dev/null)"
    [[ "${cmd}" =~ ^-?(sh|bash|zsh|fish|dash)$ ]]
}

bot_update_main() {
    local check_only=false cli_stream=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --check)
                check_only=true
                shift
                ;;
            --stream|--branch)
                cli_stream="$2"
                shift 2
                ;;
            -*)
                log_error "Unknown option: $1"
                echo "Usage: $0 [--check] [--stream <branch_name>] [<branch_name>]"
                exit 1
                ;;
            *)
                if [ -z "${cli_stream}" ]; then
                    cli_stream="$1"
                fi
                shift
                ;;
        esac
    done

    local script_dir project_root
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    project_root="$(cd "${script_dir}/.." && pwd)"
    cd "${project_root}"
    load_dotenv "${project_root}/.env"

    VENV_DIR="$(bot_setting VENV_DIR "${project_root}/.venv")"
    PYTHON_BIN="$(bot_setting PYTHON_BIN "${VENV_DIR}/bin/python")"
    PYTEST_BIN="$(bot_setting PYTEST_BIN "${VENV_DIR}/bin/pytest")"
    PIP_BIN="$(bot_setting PIP_BIN "${VENV_DIR}/bin/pip")"
    BACKUP_DIR="$(bot_setting BACKUP_DIR "${project_root}/backups")"
    DB_PATH="$(bot_setting DB_PATH "$(bot_setting DATABASE_PATH "${BOT_DEFAULT_DB}")")"
    SERVICE_NAME="$(bot_setting SERVICE_NAME "${BOT_DEFAULT_SERVICE}")"
    REPO_URL="$(bot_setting REPO_URL "https://github.com/${BOT_REPO_SLUG}.git")"
    TMUX_TARGET="$(bot_setting TMUX_TARGET "")"
    printf -v ROOT_Q '%q' "${project_root}"
    printf -v PYTHON_Q '%q' "${PYTHON_BIN}"
    START_CMD="$(bot_setting START_COMMAND "${PYTHON_Q} ${BOT_START_ARGS}")"

    if ! command -v git >/dev/null 2>&1; then
        log_error "Git is not installed on this system. Please install git (e.g. 'sudo apt install git') to enable automated updates."
        exit 1
    fi

    # Auto-initialize git if the checkout has no .git directory
    if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        log_warn "No .git repository found in ${project_root}."
        log_info "Initializing git tracking from ${REPO_URL}..."
        git init --quiet
        git remote add origin "${REPO_URL}" 2>/dev/null || git remote set-url origin "${REPO_URL}"
        log_info "Fetching upstream branch metadata..."
        git fetch origin --quiet || true
    fi

    # Migrate a legacy remote URL to the TARBot-OSS organisation
    local current_remote_url migrated_remote_url
    current_remote_url="$(git remote get-url origin 2>/dev/null || true)"
    if [ -n "${BOT_LEGACY_REMOTE_RE:-}" ] && echo "${current_remote_url}" | grep -Eq "${BOT_LEGACY_REMOTE_RE}"; then
        if echo "${current_remote_url}" | grep -q "^git@"; then
            migrated_remote_url="git@github.com:${BOT_REPO_SLUG}.git"
        else
            migrated_remote_url="https://github.com/${BOT_REPO_SLUG}.git"
        fi
        log_info "Migrating legacy remote origin (${current_remote_url}) to: ${migrated_remote_url}"
        git remote set-url origin "${migrated_remote_url}"
    fi

    log_info "Syncing remote metadata (pruning deleted branches)..."
    git fetch --prune origin --quiet || true

    # Update stream priority: CLI argument > .env > current upstream
    local configured_stream current_branch target_branch target_remote upstream_tracking
    configured_stream="${cli_stream:-$(bot_setting UPDATE_STREAM "$(bot_setting UPDATE_BRANCH auto)")}"
    current_branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")"
    if [ "${current_branch}" = "HEAD" ]; then
        current_branch="main"
    fi

    if [ -n "${configured_stream}" ] && [ "${configured_stream}" != "auto" ]; then
        target_branch="${configured_stream#origin/}"
        target_remote="origin/${target_branch}"
    else
        upstream_tracking="$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null || true)"
        if [ -n "${upstream_tracking}" ] && git rev-parse --verify --quiet "${upstream_tracking}" >/dev/null 2>&1; then
            target_remote="${upstream_tracking}"
            target_branch="${target_remote#origin/}"
        else
            if [ "${current_branch}" != "main" ]; then
                log_warn "Current branch '${current_branch}' does not exist on remote 'origin' (merged or deleted)."
                log_info "Automatically redirecting update stream to 'origin/main'..."
            fi
            target_remote="origin/main"
            target_branch="main"
        fi
    fi

    if ! git rev-parse --verify --quiet "${target_remote}" >/dev/null 2>&1; then
        if [ "${target_branch}" != "main" ]; then
            log_warn "Target remote branch '${target_remote}' was not found on origin."
            log_info "Falling back to default stream 'origin/main'..."
            target_remote="origin/main"
            target_branch="main"
            git fetch origin main --quiet || true
        fi
    fi

    log_info "Target update stream: ${target_remote} (Local branch: ${current_branch})"

    local local_hash remote_hash behind_count
    local_hash="$(git rev-parse HEAD 2>/dev/null || echo "uninitialized")"
    remote_hash="$(git rev-parse "${target_remote}" 2>/dev/null || true)"

    if [ -z "${remote_hash}" ]; then
        log_error "Remote branch '${target_remote}' not found on remote. Check branch name or network connection."
        exit 1
    fi

    if [ "${local_hash}" != "uninitialized" ]; then
        behind_count="$(git rev-list --count HEAD.."${target_remote}" 2>/dev/null || echo "0")"
        if [ "${local_hash}" = "${remote_hash}" ] && [ "${current_branch}" = "${target_branch}" ]; then
            log_success "${BOT_NAME} is already on the latest version of stream '${target_remote}' (${local_hash:0:7})."
            if [ "${check_only}" = true ]; then
                exit 0
            fi
        fi

        if [ "${behind_count}" -gt 0 ] || [ "${current_branch}" != "${target_branch}" ]; then
            log_warn "Upstream '${target_remote}' is ${behind_count} commit(s) ahead (${local_hash:0:7} -> ${remote_hash:0:7})."
        fi
    else
        behind_count="all"
        log_warn "Repository was not previously git-tracked. Ready to sync with '${target_remote}' (${remote_hash:0:7})."
    fi

    if [ "${check_only}" = true ]; then
        if [ "${behind_count}" = "all" ] || [ "${behind_count}" -gt 0 ] || [ "${current_branch}" != "${target_branch}" ]; then
            echo -e "\n🔔 ${YELLOW}Update available!${NC} Run './scripts/update.sh ${target_branch}' on this server to apply."
        fi
        exit 0
    fi

    # ------------------------------------------------------------------------------
    # STEP 1: Pre-Update Database Backup
    # ------------------------------------------------------------------------------
    local updates_backup_dir timestamp snapshot_path
    updates_backup_dir="${BACKUP_DIR}/updates"
    mkdir -p "${updates_backup_dir}"
    timestamp="$(date +%Y%m%d_%H%M%S)"
    snapshot_path="${updates_backup_dir}/${BOT_BACKUP_PREFIX}_pre_update_${timestamp}.db"

    log_info "[1/5] Creating pre-update database backup in ${updates_backup_dir}..."
    if [ -f "${DB_PATH}" ]; then
        ${PYTHON_BIN} -c "
import os, sqlite3, sys
try:
    src = sqlite3.connect('${DB_PATH}')
    dst = sqlite3.connect('${snapshot_path}')
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    print('Snapshot created successfully.')
except Exception as e:
    print(f'Backup error: {e}', file=sys.stderr)
    sys.exit(1)

# Keep the 5 most recent pre-update snapshots
updates_dir = '${updates_backup_dir}'
max_update_backups = 5
if os.path.exists(updates_dir):
    files = [
        os.path.join(updates_dir, f)
        for f in os.listdir(updates_dir)
        if os.path.isfile(os.path.join(updates_dir, f)) and f.endswith('.db')
    ]
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for old_f in files[max_update_backups:]:
        try:
            os.remove(old_f)
            print(f'Pruned older pre-update snapshot: {os.path.basename(old_f)}')
        except Exception:
            pass
"
        log_success "Database snapshot saved to ${snapshot_path} (retained up to 5 most recent in updates/)"
    else
        snapshot_path=""
        log_info "No existing database file at ${DB_PATH}. Skipping backup."
    fi

    # ------------------------------------------------------------------------------
    # STEP 2: Pull Upstream Changes & Switch Stream If Requested
    # ------------------------------------------------------------------------------
    log_info "[2/5] Updating code to stream ${target_remote}..."
    local prev_branch="${current_branch}" prev_commit="${local_hash}"

    rollback() {
        log_error "Update failed! Initiating rollback..."
        if [ "${prev_commit}" != "uninitialized" ]; then
            git checkout "${prev_branch}" 2>/dev/null || true
            git reset --hard "${prev_commit}" 2>/dev/null || true
        fi

        if [ -n "${snapshot_path}" ] && [ -f "${snapshot_path}" ] && [ -f "${DB_PATH}" ]; then
            log_info "Restoring database from ${snapshot_path}..."
            cp -f "${snapshot_path}" "${DB_PATH}"
        fi

        log_warn "Rollback complete. Python packages installed during the failed update are not reverted."
        exit 1
    }

    trap rollback ERR

    if [ "${local_hash}" = "uninitialized" ]; then
        log_info "Linking working tree to '${target_remote}'..."
        git checkout -B "${target_branch}" "${target_remote}"
    else
        if [ "${current_branch}" != "${target_branch}" ]; then
            log_info "Switching branch: '${current_branch}' -> '${target_branch}'..."
            if git show-ref --verify --quiet "refs/heads/${target_branch}"; then
                git checkout "${target_branch}"
            else
                git checkout -b "${target_branch}" --track "${target_remote}"
            fi
        fi
        git pull origin "${target_branch}" --ff-only
    fi

    local new_commit
    new_commit="$(git rev-parse HEAD)"
    log_success "Successfully pulled code (now at commit ${new_commit:0:7} on branch '${target_branch}')."

    # ------------------------------------------------------------------------------
    # STEP 3: Sync Python Dependencies
    # ------------------------------------------------------------------------------
    log_info "[3/5] Updating dependencies..."
    if [ ! -f "${PIP_BIN}" ] || [ ! -x "${PYTHON_BIN}" ]; then
        log_warn "Virtual environment not found at '${VENV_DIR}'. Initializing a fresh virtualenv..."
        python3 -m venv "${VENV_DIR}"
        "${PIP_BIN}" install --upgrade pip --quiet
    fi

    ${PIP_BIN} install -r requirements.txt --upgrade --quiet

    # ------------------------------------------------------------------------------
    # STEP 4: Run Automated Pre-flight Tests
    # ------------------------------------------------------------------------------
    log_info "[4/5] Running automated pre-flight test suite..."
    if [ -x "${PYTEST_BIN}" ]; then
        ${PYTEST_BIN} -q
        log_success "All pre-flight tests passed successfully!"
    else
        log_warn "pytest not found in virtualenv. Running syntax compile check instead..."
        ${PYTHON_BIN} -m compileall -q ${BOT_COMPILE_TARGETS}
    fi

    trap - ERR

    # ------------------------------------------------------------------------------
    # STEP 5: Restart Service
    # ------------------------------------------------------------------------------
    log_info "[5/5] Checking and restarting service '${SERVICE_NAME}'..."

    local restarted=false
    if command -v systemctl >/dev/null 2>&1; then
        # 1. Systemd user service
        if systemctl --user is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
            log_info "Restarting systemd user service '${SERVICE_NAME}'..."
            systemctl --user restart "${SERVICE_NAME}"
            log_success "Systemd user service '${SERVICE_NAME}' restarted."
            restarted=true
        fi
        # 2. System-wide service
        if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
            log_info "Restarting system-wide service '${SERVICE_NAME}'..."
            if [ "$EUID" -eq 0 ]; then
                systemctl restart "${SERVICE_NAME}"
            else
                sudo systemctl restart "${SERVICE_NAME}" 2>/dev/null || systemctl restart "${SERVICE_NAME}"
            fi
            log_success "System service '${SERVICE_NAME}' restarted."
            restarted=true
        fi
    fi

    if [ "${restarted}" = false ] && [ -n "${TMUX_TARGET}" ]; then
        if restart_in_tmux "${TMUX_TARGET}"; then
            restarted=true
        fi
    fi

    if [ "${restarted}" = false ]; then
        log_info "No active systemd service '${SERVICE_NAME}' detected."
        log_info "If running manually (e.g. in tmux or screen), restart your bot process now:"
        echo -e "      ${GREEN}python ${BOT_START_ARGS}${NC}"
    fi

    echo -e "\n=============================================================================="
    if [ -n "${TMUX_TARGET}" ] && [ "${restarted}" = false ]; then
        log_warn "Code and dependencies are updated, but the bot was NOT restarted. Restart it manually."
    else
        log_success "${BOT_NAME} update on stream '${target_remote}' (${new_commit:0:7}) completed cleanly!"
    fi
    if [ -n "${snapshot_path}" ]; then
        log_info "Pre-update backup preserved at: ${snapshot_path}"
    fi
    echo -e "==============================================================================\n"
}
