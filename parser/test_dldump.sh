#!/usr/bin/env bash
# Run from Linux or Git Bash on Windows; the demo is mounted read-only.
set -euo pipefail
if [[ $# != 1 || ! -f $1 ]]; then
    echo "Usage: $0 <demo.dem>" >&2
    exit 2
fi
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
demo=$(cd "$(dirname "$1")" && printf '%s/%s' "$PWD" "$(basename "$1")")
if command -v cygpath >/dev/null; then
    root=$(cygpath -m "$root")
    demo=$(cygpath -m "$demo")
fi
# Prevent Git Bash from rewriting container paths in Docker arguments.
export MSYS_NO_PATHCONV=1
image=${DLDUMP_IMAGE:-deadlock-stats-parser:local}
docker build -f "$root/parser/Dockerfile" -t "$image" "$root"
docker run --rm --mount "type=bind,source=$demo,target=/demo.dem,readonly" \
    --entrypoint /bin/sh "$image" -ec '
    mkdir /tmp/result
    /out/dldump /demo.dem /tmp/result
    echo "dldump exit: 0"
    awk -F "\t" '\''$2 == 316 { found = 1 } END { exit !found }'\'' /tmp/result/msgs.tsv
    grep '\''"tag":"final"'\'' /tmp/result/ents.jsonl | grep -q m_iszPlayerName
    test -s /tmp/result/classes.tsv
    lines=$(wc -l < /tmp/result/msgs.tsv)
    echo "msgs.tsv lines: $lines"
    if [ "$1" = 133162474.dem ]; then test "$lines" -eq 142588; fi
    echo "PASS: exit 0; message 316; final player names; classes.tsv"
    ' sh "$(basename "$demo")"
