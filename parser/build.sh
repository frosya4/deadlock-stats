#!/usr/bin/env bash
# Requires git, cargo, a C/C++ toolchain and protoc with its well-known protos
# (Debian packages: protobuf-compiler libprotobuf-dev).
# Usage: parser/build.sh [output-directory]; sources/cache stay outside the repo.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
patch_dir=$root/prototype/haste-patch
commit=$(tr -d '\r\n' < "$patch_dir/HASTE_COMMIT")
source_dir=${HASTE_DIR:-${TMPDIR:-/tmp}/deadlock-stats-haste-$commit}
out_dir=${1:-/out}
if [[ ! -d $source_dir/.git ]]; then
    git clone https://github.com/blukai/haste.git "$source_dir"
fi
cd "$source_dir"
if [[ $(git rev-parse HEAD) != "$commit" ]]; then
    git fetch origin "$commit"
    git checkout --detach "$commit"
fi
# A second invocation accepts exactly the already-applied patch.
if git apply --check "$patch_dir/haste.patch" 2>/dev/null; then
    git apply "$patch_dir/haste.patch"
else
    git apply --reverse --check "$patch_dir/haste.patch"
fi
mkdir -p examples "$out_dir"
cp "$patch_dir/dldump.rs" examples/dldump.rs
cargo build --release --example dldump --features "deadlock preserve-metadata"
install -m 755 target/release/examples/dldump "$out_dir/dldump"
