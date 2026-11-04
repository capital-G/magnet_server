#!/bin/sh
# Builds the docs of one package by hand with the builder image (docs_builder/, see
# docs_plan.md), the way the docs task does:
#
#   scripts/build_docs.sh NAME DEPENDENCY OUT_DIR
#
#   scripts/build_docs.sh foo '"=1.0.0"' /tmp/docs/foo                 # from the index
#   scripts/build_docs.sh 3Dj '{ legacy-quark = true }' /tmp/docs/3dj  # a legacy quark
#
# DEPENDENCY is the value of the package in the [dependencies] of a temporary project.
# 1. install (with network): `magnet install` fetches the package and its dependencies
#    into WORK/magnet_packages
# 2. render (no network, magnet_packages read-only at /packages, OUT_DIR at /out,
#    limits): render.scd writes the HTML and docmap.json
#
# Environment: MAGNET_DOCS_IMAGE (default magnet-docs-builder:sc3.14.1, `make
# docs-builder`), MAGNET_GIT_INDEX_URL (default: the local stack's git daemon), other
# MAGNET_*_URL are passed to `magnet install`, MAGNET_DOCS_WORK (default: a temp dir,
# kept for inspection), MAGNET_DOCS_TIMEOUT (seconds, default 300).
set -eu

if [ $# -ne 3 ]; then
    sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
fi
NAME=$1
DEPENDENCY=$2
IMAGE=${MAGNET_DOCS_IMAGE:-magnet-docs-builder:sc3.14.1}
TIMEOUT=${MAGNET_DOCS_TIMEOUT:-300}
# the index's normalisation, also the folder within magnet_packages
PACKAGE=$(printf '%s' "$NAME" | tr 'A-Z_' 'a-z-')

mkdir -p "$3"
OUT=$(cd "$3" && pwd -P)
WORK=${MAGNET_DOCS_WORK:-$(mktemp -d "${TMPDIR:-/tmp}/magnet-docs.XXXXXX")}
mkdir -p "$WORK"
WORK=$(cd "$WORK" && pwd -P)

cat > "$WORK/magnet.toml" <<EOF
magnet-version = 1

[package]
name = "magnet-docs-build"
version = "0.0.0"

[dependencies]
$NAME = $DEPENDENCY
EOF
# the host's user inside the containers, so the files stay accessible;
# HOME on a tmpfs (sclang writes its user app support folder there)
RUN="podman run --rm --userns=keep-id --read-only --tmpfs /tmp --env HOME=/tmp/home"

echo "== install $NAME into $WORK"
$RUN \
    --volume "$WORK:$WORK" --workdir "$WORK" \
    --env MAGNET_GIT_INDEX_URL="${MAGNET_GIT_INDEX_URL:-git://host.containers.internal:9418/magnet_index.git}" \
    ${MAGNET_HTTP_INDEX_URL:+--env MAGNET_HTTP_INDEX_URL="$MAGNET_HTTP_INDEX_URL"} \
    ${MAGNET_API_URL:+--env MAGNET_API_URL="$MAGNET_API_URL"} \
    "$IMAGE" magnet install

echo "== render $PACKAGE into $OUT"
# runs the package's code (class library compilation, *initClass, StartUp): no network,
# no capabilities, only /out writable, limited memory/processes/time; sclang compiles
# SuperCollider's class library and the installed packages only (-a: no default paths)
$RUN \
    --network none --cap-drop ALL --security-opt no-new-privileges \
    --memory 2g --pids-limit 256 --timeout "$TIMEOUT" \
    --volume "$WORK/magnet_packages:/packages:ro" --volume "$OUT:/out" \
    "$IMAGE" sclang -a \
    --include-path /usr/local/share/SuperCollider/SCClassLibrary \
    --include-path /packages \
    /opt/magnet/render.scd /packages "$PACKAGE" /out

echo "== done: $OUT/docmap.json (work dir $WORK)"
