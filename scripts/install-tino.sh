#!/usr/bin/env bash
set -euo pipefail

REPO="${TINO_REPO:-ChoKhoOu/TinoHelm}"
VERSION="${TINO_VERSION:-nightly}"
BINDIR="${BINDIR:-}"

usage() {
  cat <<'EOF'
Usage: install-tino.sh [--nightly] [--version <tag>] [--bindir <dir>]

Environment overrides:
  TINO_REPO=owner/repo
  TINO_VERSION=<tag|nightly>
  BINDIR=/install/dir

Defaults:
  Installs the moving nightly release unless --version <tag> is provided.
  Supported platforms: Linux and macOS. Windows is not supported.
EOF
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

path_contains_dir() {
  case ":$PATH:" in
    *:"$1":*) return 0 ;;
    *) return 1 ;;
  esac
}

default_bindir() {
  if [ -w /usr/local/bin ]; then
    printf '%s\n' /usr/local/bin
  else
    printf '%s\n' "$HOME/.local/bin"
  fi
}

detect_target() {
  local os arch
  os=$(uname -s)
  arch=$(uname -m)

  case "$os" in
    Linux) ;;
    Darwin) ;;
    *)
      fail "unsupported operating system: $os (supported: Linux and macOS)"
      ;;
  esac

  case "$os" in
    Linux)
      case "$arch" in
        x86_64|amd64)
          printf '%s\n' x86_64-unknown-linux-gnu
          ;;
        aarch64|arm64)
          printf '%s\n' aarch64-unknown-linux-gnu
          ;;
        *)
          fail "unsupported architecture for Linux: $arch"
          ;;
      esac
      ;;
    Darwin)
      case "$arch" in
        x86_64|amd64)
          printf '%s\n' x86_64-apple-darwin
          ;;
        aarch64|arm64)
          printf '%s\n' aarch64-apple-darwin
          ;;
        *)
          fail "unsupported architecture for macOS: $arch"
          ;;
      esac
      ;;
  esac
}

gh_is_authenticated() {
  have_cmd gh && gh auth status >/dev/null 2>&1
}

download_with_gh() {
  local asset="$1" dest="$2"
  gh release download "$VERSION" --repo "$REPO" --pattern "$asset" --output "$dest"
}

download_with_curl() {
  local asset="$1" dest="$2" url
  have_cmd curl || fail "neither authenticated gh nor curl is available for downloads"
  url="https://github.com/$REPO/releases/download/$VERSION/$asset"
  curl -fsSL "$url" -o "$dest"
}

install_binary() {
  local src="$1" bindir="$2" final_path tmp_path
  mkdir -p "$bindir"
  final_path="$bindir/tino"
  tmp_path="$bindir/.tino.tmp.$$"
  cp "$src" "$tmp_path"
  chmod +x "$tmp_path"
  mv "$tmp_path" "$final_path"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --nightly)
      VERSION=nightly
      shift
      ;;
    --version)
      [ "$#" -ge 2 ] || fail "--version requires a value"
      VERSION="$2"
      shift 2
      ;;
    --bindir)
      [ "$#" -ge 2 ] || fail "--bindir requires a value"
      BINDIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

BINDIR="${BINDIR:-$(default_bindir)}"
TARGET="$(detect_target)"
ASSET="tino-${TARGET}.tar.gz"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

archive="$tmpdir/$ASSET"
if gh_is_authenticated; then
  download_with_gh "$ASSET" "$archive"
else
  download_with_curl "$ASSET" "$archive"
fi

tar -xzf "$archive" -C "$tmpdir"
[ -x "$tmpdir/tino" ] || fail "archive did not contain an executable tino binary"
"$tmpdir/tino" version >/dev/null

install_binary "$tmpdir/tino" "$BINDIR"
printf 'installed: %s/tino\n' "$BINDIR"
if ! path_contains_dir "$BINDIR"; then
  printf 'warning: %s is not on PATH; add it before running tino from arbitrary directories.\n' "$BINDIR" >&2
fi
