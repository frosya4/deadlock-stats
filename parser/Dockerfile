# Build from the repository root:
# docker build -f parser/Dockerfile -t deadlock-stats-parser:local .
FROM rust:1.99.1-bookworm AS builder
RUN apt-get update \
    && apt-get install -y --no-install-recommends protobuf-compiler libprotobuf-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY prototype/haste-patch/ prototype/haste-patch/
COPY parser/build.sh parser/build.sh
RUN bash parser/build.sh /out

# Debian bookworm glibc baseline; no Rust toolchain needed at runtime.
# The root application image can COPY --from=<this image> /out/dldump ...
FROM debian:bookworm-slim AS artifact
COPY --from=builder /out/dldump /out/dldump
ENTRYPOINT ["/out/dldump"]
