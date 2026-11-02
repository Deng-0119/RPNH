#!/usr/bin/env bash
# Install public RPNH source into a new, isolated directory. No model calls.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash install.sh [--prefix DIRECTORY] [--ref REF]

Requires Linux/WSL2, Git, and Python 3.11+ with venv/pip support.
Downloads public RPNH source and Python dependencies; no model credentials needed.
Default directory: $HOME/.local/share/rpnh (must not already exist).
Default ref: main. Use a full commit SHA for a reproducible source selection.
Installs RPNH and its native demo plugin into PREFIX/venv; source is PREFIX/source.
Does not use sudo, edit shell startup files, or modify system Python.
EOF
}

die() { printf 'RPNH install: %s\n' "$*" >&2; exit 1; }
prefix="${HOME:?HOME must be set}/.local/share/rpnh"
ref=main
while (($#)); do
  case "$1" in
    --prefix|--ref)
      (($# >= 2)) || die "$1 requires a value"
      [[ -n "$2" ]] || die "$1 requires a nonempty value"
      if [[ "$1" == --prefix ]]; then prefix=$2; else ref=$2; fi
      shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1 (use --help)" ;;
  esac
done

[[ "$(uname -s)" == Linux ]] || die 'Linux or WSL2 is required'
command -v git >/dev/null || die 'Git is required'
command -v python3 >/dev/null || die 'Python 3.11+ is required'
python3 -I -c 'import sys, venv, ensurepip; assert sys.version_info >= (3, 11), "Python 3.11+ is required"' \
  || die 'Python 3.11+ with venv/ensurepip is required'
[[ "$ref" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || die 'invalid ref'
[[ "$prefix" == /* ]] || prefix="$PWD/$prefix"
[[ ! -e "$prefix" && ! -L "$prefix" ]] || die "directory already exists; choose a new --prefix: $prefix"
parent=$(dirname -- "$prefix")
mkdir -p -- "$parent"
# Atomic creation refuses an existing directory, including concurrent installs.
mkdir -- "$prefix" || die "cannot create new directory: $prefix"
trap 'printf "Installation stopped. Files are retained at %s; inspect them and choose a new --prefix before retrying.\n" "$prefix" >&2' ERR

git init -q "$prefix/source"
git -C "$prefix/source" remote add origin https://github.com/Deng-0119/RPNH.git
git -C "$prefix/source" fetch --depth 1 origin "$ref"
git -C "$prefix/source" checkout -q --detach FETCH_HEAD
git -C "$prefix/source" rev-parse HEAD | tee "$prefix/SOURCE_COMMIT"
python3 -I -m venv "$prefix/venv"
"$prefix/venv/bin/python" -I -m pip --isolated install "$prefix/source"
"$prefix/venv/bin/python" -I -m pip --isolated install "$prefix/source/examples/native_plugin"
# Only CLI parsing: no provider configuration or model call.
(cd "$prefix" && "$prefix/venv/bin/rpnh" --help >/dev/null)
trap - ERR
printf '\nInstalled RPNH source at %s\n' "$prefix"
printf 'Source commit: '; cat "$prefix/SOURCE_COMMIT"
printf 'Activate: source %q\n' "$prefix/venv/bin/activate"
printf 'Examples: cd %q\n' "$prefix/source"
printf 'Then follow the README native-add example. No model was called.\n'
