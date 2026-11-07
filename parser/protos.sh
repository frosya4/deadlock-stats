#!/usr/bin/env bash
# Requires git and protoc with well-known protos (Debian packages:
# git protobuf-compiler libprotobuf-dev).
# Usage: parser/protos.sh [output-directory]; default: app/proto.
# Generated modules retain upstream absolute imports, like the vendored files.
# Their SteamLearn dependency is supplied separately by app/pipeline/_vendor;
# this script preserves the existing app/proto file set.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
rev=$(tr -d '\r\n' < "$root/prototype/VALVEPROTOS_REV")
source_dir=${VALVEPROTOS_DIR:-${TMPDIR:-/tmp}/deadlock-stats-valveprotos-$rev}
out_dir=${1:-$root/app/proto}
command -v protoc >/dev/null
if [[ ! -d $source_dir/.git ]]; then
    git clone https://github.com/blukai/valveprotos-rs.git "$source_dir"
fi
if [[ $(git -C "$source_dir" rev-parse HEAD) != "$rev" ]]; then
    git -C "$source_dir" fetch origin "$rev"
    git -C "$source_dir" checkout --detach "$rev"
fi
protos=$source_dir/protos
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
# Only the vendored dependency closure, rather than every game's messages.
protoc -I "$protos/common" -I "$protos/gcsdk" -I "$protos/deadlock" \
    --python_out="$stage" \
    citadel_gcmessages_client.proto citadel_gcmessages_common.proto \
    citadel_usermessages.proto demo.proto gameevents.proto \
    gcsdk_gcmessages.proto netmessages.proto networkbasetypes.proto \
    network_connection.proto steammessages.proto usermessages.proto
mkdir -p "$out_dir"
cp "$stage/"*_pb2.py "$out_dir/"
touch "$out_dir/__init__.py"
echo "Generated 11 protobuf modules in $out_dir ($(protoc --version))"
