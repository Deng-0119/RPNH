#!/usr/bin/env bash
# Thin source/manual or explicitly selected offline managed-candidate entry.
# Managed transactions remain entirely in the selected RPNH Python manager.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash install.sh [--prefix DIRECTORY] [--ref REF | --source DIRECTORY]
                       [--python EXECUTABLE] [--check]
       bash install.sh --prefix DEDICATED_ROOT --candidate-wheel WHEEL
                       --materials JSON --python MANAGER_PYTHON
                       [--components JSON] [--select FRONTEND] [--check]

Managed candidate mode requires an existing RPNH management interpreter and
explicit local wheel/materials. It forwards to that interpreter's update
plan/apply executor; --check validates a plan without creating a root.
--components specifies the complete frontend set; omission preserves the
current set (Basic for bootstrap). --select chooses the activation display.
Managed mode is offline and requires an explicit --prefix; it does not mix
with --source/--ref. Apply retains the manager's terminal confirmation.
Published releases and cross-version activation are not enabled by this mode.

Install public RPNH source, or an explicitly selected local checkout.
Linux requires Python 3.11+; the macOS candidate requires Python 3.13+.
Python must provide venv/ensurepip and the required POSIX process APIs.
Default interpreter: python3. Use --python to select another installation.
Default directory: $HOME/.local/share/rpnh (must not already exist).
Default public ref: main. An exact commit selects a fixed source revision.
--source uses local source as supplied, including any uncommitted changes.
--check reports prerequisites only; it does not download or install anything.

Installs RPNH and its native demo plugin into PREFIX/venv. Public source is
kept in PREFIX/source; a --source directory is used at its existing location.
This is a manual/source installation, not a managed-v2 installation.
Installation can download Python dependencies. No model is called.
The script does not use sudo, edit shell startup files or replace system Python.
Platform diagnostics describe prerequisites, not native runtime qualification.
EOF
}

die() { printf 'RPNH install: %s\n' "$*" >&2; exit 1; }
prefix="${HOME:?HOME must be set}/.local/share/rpnh"
prefix_selected=false
ref=main
ref_selected=false
source_input=
python_input=python3
check_only=false
candidate_wheel=
materials_file=
components_file=
selected_frontend=
while (($#)); do
  case "$1" in
    --prefix|--ref|--source|--python|--candidate-wheel|--materials|--components|--select)
      (($# >= 2)) || die "$1 requires a value"
      [[ -n "$2" ]] || die "$1 requires a nonempty value"
      case "$1" in
        --prefix) prefix=$2; prefix_selected=true ;;
        --ref) ref=$2; ref_selected=true ;;
        --source) source_input=$2 ;;
        --python) python_input=$2 ;;
        --candidate-wheel) candidate_wheel=$2 ;;
        --materials) materials_file=$2 ;;
        --components) components_file=$2 ;;
        --select) selected_frontend=$2 ;;
      esac
      shift 2 ;;
    --check) check_only=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1 (use --help)" ;;
  esac
done

managed_candidate=false
if [[ -n "$candidate_wheel" || -n "$materials_file" ]]; then
  managed_candidate=true
  [[ -n "$candidate_wheel" && -n "$materials_file" ]] \
    || die 'managed candidate mode requires both --candidate-wheel and --materials'
  [[ "$prefix_selected" == true ]] || die 'managed candidate mode requires an explicit --prefix'
  [[ -z "$source_input" && "$ref_selected" == false ]] \
    || die 'managed candidate mode cannot use --source or --ref'
  [[ "$check_only" == false || -z "$selected_frontend" ]] \
    || die '--select applies during activation; omit it for --check'
else
  [[ -z "$components_file" && -z "$selected_frontend" ]] \
    || die '--components and --select require managed candidate mode'
fi

[[ -z "$source_input" || "$ref_selected" == false ]] \
  || die '--source and --ref cannot be used together'
[[ "$ref" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || die 'invalid ref'
system_name=$(uname -s)
case "$system_name" in
  Linux|Darwin) ;;
  *) die "unsupported platform: $system_name; use Linux or the macOS candidate" ;;
esac
python_bin=$(command -v "$python_input") || die "Python executable not found: $python_input"
[[ -x "$python_bin" && ! -d "$python_bin" ]] || die "not an executable Python path: $python_bin"
# Resolve a relative executable once; later phases use this exact interpreter.
[[ "$python_bin" == /* ]] || python_bin="$PWD/$python_bin"
if [[ "$managed_candidate" == true ]]; then
  command_args=(apply --root "$prefix" --candidate-wheel "$candidate_wheel" --materials "$materials_file")
  [[ "$check_only" == false ]] || command_args[0]=plan
  [[ -z "$components_file" ]] || command_args+=(--components "$components_file")
  [[ -z "$selected_frontend" ]] || command_args+=(--select "$selected_frontend")
  # No shell-created root, dependency resolver, alternate transaction logic,
  # source import path or noninteractive confirmation bypass.
  exec "$python_bin" -I -m cpn.rpnh.update_cli "${command_args[@]}"
fi
"$python_bin" -I - "$system_name" <<'PY' || exit 1
import platform
import sys

minimum = (3, 13) if sys.argv[1] == "Darwin" else (3, 11)
if platform.system() != sys.argv[1]:
    raise SystemExit("RPNH install: Python and shell report different operating systems")
if sys.version_info < minimum:
    raise SystemExit("RPNH install: Python %s.%s+ is required on %s" % (*minimum, sys.argv[1]))
try:
    import ensurepip
    import venv
except ImportError as exc:
    raise SystemExit("RPNH install: Python prerequisite is unavailable: " + str(exc))
print("Platform: %s %s" % (platform.system(), platform.machine()))
print("Python: %s (%s)" % (platform.python_version(), sys.executable))
print("Install mode: manual/source")
PY

if [[ -n "$source_input" ]]; then
  [[ -d "$source_input" ]] || die "source directory not found: $source_input"
  install_source=$(cd -- "$source_input" && pwd -P)
  [[ -f "$install_source/pyproject.toml" && -d "$install_source/cpn" ]] \
    || die 'selected source must contain pyproject.toml and cpn/'
  [[ -f "$install_source/examples/native_plugin/pyproject.toml" ]] \
    || die 'selected source is missing examples/native_plugin/pyproject.toml'
else
  command -v git >/dev/null || die 'Git is required for public source installation'
  install_source=
fi

# The selected source owns runtime capability checks. Bootstrap deliberately
# knows only the OS/Python floor and venv prerequisites, not frontend profiles.
check_source_platform() {
  local source_root=$1
  if [[ -f "$source_root/cpn/rpnh/platform_support.py" ]]; then
    "$python_bin" -I "$source_root/cpn/rpnh/platform_support.py" --json
  elif [[ "$system_name" == Darwin ]]; then
    die 'selected source has no macOS platform preflight; choose the prepared macOS candidate source'
  fi
}
if [[ -n "$install_source" ]]; then
  check_source_platform "$install_source"
fi

if [[ "$check_only" == true ]]; then
  printf 'Available prerequisite checks passed; no installation was performed.\n'
  if [[ -z "$install_source" ]]; then
    printf 'Use --source with --check to include the selected source runtime checks.\n'
  fi
  exit 0
fi

[[ "$prefix" == /* ]] || prefix="$PWD/$prefix"
[[ ! -e "$prefix" && ! -L "$prefix" ]] \
  || die "directory already exists; choose a new --prefix: $prefix"
parent=$(dirname "$prefix")
mkdir -p -- "$parent"
# Atomic creation refuses an existing directory, including concurrent installs.
mkdir -- "$prefix" || die "cannot create new directory: $prefix"
trap 'printf "Installation stopped. Files are retained at %s; inspect them and choose a new --prefix before retrying.\n" "$prefix" >&2' ERR

if [[ -z "$install_source" ]]; then
  install_source="$prefix/source"
  git init -q "$install_source"
  git -C "$install_source" remote add origin https://github.com/Deng-0119/RPNH.git
  git -C "$install_source" fetch --depth 1 origin "$ref"
  git -C "$install_source" checkout -q --detach FETCH_HEAD
  git -C "$install_source" rev-parse HEAD > "$prefix/SOURCE_COMMIT"
  check_source_platform "$install_source"
else
  printf '%s\n' "$install_source" > "$prefix/SOURCE_PATH"
fi

"$python_bin" -I -m venv "$prefix/venv"
"$prefix/venv/bin/python" -I -m pip --isolated install "$install_source"
"$prefix/venv/bin/python" -I -m pip --isolated install "$install_source/examples/native_plugin"
if [[ -f "$install_source/cpn/rpnh/platform_support.py" ]]; then
  (cd "$prefix" && "$prefix/venv/bin/python" -I -m cpn.rpnh.platform_support --json)
fi
# Only CLI parsing: no provider configuration or model call.
(cd "$prefix" && "$prefix/venv/bin/rpnh" --help >/dev/null)
trap - ERR
printf '\nInstalled RPNH manual/source environment at %s\n' "$prefix"
if [[ -f "$prefix/SOURCE_COMMIT" ]]; then
  printf 'Source commit: '
  cat "$prefix/SOURCE_COMMIT"
else
  printf 'Source directory: %s\n' "$install_source"
fi
printf 'Activate: source %q\n' "$prefix/venv/bin/activate"
printf 'Examples: cd %q\n' "$install_source"
printf 'Then follow the README native-add example. No model was called.\n'
