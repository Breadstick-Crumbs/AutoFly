#!/usr/bin/env sh
set -eu

GO_VERSION="1.26.5"
FLIGHT_GOAT_COMMIT="854c0465aaa9c275485338c2be7ef0bcaddc4e89"

case "$(uname -m)" in
  x86_64|amd64)
    GO_ARCH="amd64"
    GO_SHA256="5c2c3b16caefa1d968a94c1daca04a7ca301a496d9b086e17ad77bb81393f053"
    ;;
  aarch64|arm64)
    GO_ARCH="arm64"
    GO_SHA256="fe4789e92b1f33358680864bbe8704289e7bb5fc207d80623c308935bd696d49"
    ;;
  *)
    echo "Unsupported architecture: $(uname -m). Supported: amd64, arm64." >&2
    exit 2
    ;;
esac

for command_name in curl tar sha256sum; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Required command not found: $command_name" >&2
    exit 2
  fi
done

DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/autofly"
BIN_ROOT="${XDG_BIN_HOME:-$HOME/.local/bin}"
GO_ROOT="$DATA_ROOT/toolchains/go-$GO_VERSION"
mkdir -p "$DATA_ROOT/toolchains" "$BIN_ROOT"

if [ ! -x "$GO_ROOT/bin/go" ]; then
  TEMP_ROOT="$(mktemp -d)"
  trap 'rm -rf "$TEMP_ROOT"' EXIT HUP INT TERM
  ARCHIVE="$TEMP_ROOT/go.tar.gz"
  curl -fL --retry 3 -o "$ARCHIVE" \
    "https://go.dev/dl/go${GO_VERSION}.linux-${GO_ARCH}.tar.gz"
  echo "$GO_SHA256  $ARCHIVE" | sha256sum -c -
  tar -C "$TEMP_ROOT" -xzf "$ARCHIVE"
  mv "$TEMP_ROOT/go" "$GO_ROOT"
  trap - EXIT HUP INT TERM
  rm -rf "$TEMP_ROOT"
fi

GOBIN="$BIN_ROOT" "$GO_ROOT/bin/go" install \
  "github.com/mvanhorn/printing-press-library/library/travel/flight-goat/cmd/flight-goat-pp-cli@$FLIGHT_GOAT_COMMIT"

VERSION_OUTPUT="$($BIN_ROOT/flight-goat-pp-cli --version)"
case "$VERSION_OUTPUT" in
  *"2026.8.1"*) ;;
  *)
    echo "Unexpected Flight GOAT version: $VERSION_OUTPUT" >&2
    exit 3
    ;;
esac

echo "Installed $VERSION_OUTPUT at $BIN_ROOT/flight-goat-pp-cli"
echo "Ensure $BIN_ROOT is on PATH."
