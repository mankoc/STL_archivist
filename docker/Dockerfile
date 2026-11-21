# StlArchivist server (rust/stl-server) and web client (web/), with pdfium (PDF
# previews) and ONNX Runtime (AI embeddings). See docs/deployment.md → "Option C: Docker".
# Each release is built and pushed to Docker Hub by .github/workflows/docker.yml.
# The build context is the repository root (Dockerfile.dockerignore beside this file
# applies to it), so build from there:
#
#   docker build -f docker/Dockerfile -t stlarchivist .
#   docker run -d --name stl -p 8770:8770 -v /srv/stl-data:/data -v /srv/STL:/library:ro stlarchivist
#   docker exec -it stl stl-server create-admin admin
#
# Archives are read where the library's paths point: mount the archive folders at
# the paths the library recorded (or crawl them anew at their container paths).
# RAR archives need UnRAR, which is not free software and not included: add it in
# a derived image and set STL_UNRAR_PATH.

# As in rust-toolchain.toml (this image's build sees only rust/, so keep the two the same).
ARG RUST_VERSION=1.99.0
FROM rust:${RUST_VERSION}-trixie AS build
WORKDIR /src
COPY rust/ rust/
# "nightly <commit>" for a development build (shown beside the version); empty for a release.
ARG STL_BUILD=
RUN cd rust && STL_BUILD="$STL_BUILD" cargo build --release -p stl-server

# The web client (its API types are committed, so no Rust is needed here).
FROM node:24-trixie-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY web/ ./
RUN npm run build

FROM debian:trixie-slim
# The same builds and SHA-256 as the server's components (rust/stl-server/src/components.rs):
# change a version and its hash together.
ARG PDFIUM_BUILD=7881
ARG PDFIUM_SHA256=1470e21b8b4a3b4ad7f85684e2da11d94f3b69a86d81dee11b9b6709d927ac1d
ARG ORT_VERSION=1.22.0
ARG ORT_SHA256=8344d55f93d5bc5021ce342db50f62079daf39aaafb5d311a451846228be49b3
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && mkdir -p /opt/pdfium \
 && curl -fsSL --retry 5 --retry-all-errors --retry-delay 15 -o /tmp/pdfium.tgz "https://github.com/bblanchon/pdfium-binaries/releases/download/chromium/${PDFIUM_BUILD}/pdfium-linux-x64.tgz" \
 && curl -fsSL --retry 5 --retry-all-errors --retry-delay 15 -o /tmp/ort.tgz "https://github.com/microsoft/onnxruntime/releases/download/v${ORT_VERSION}/onnxruntime-linux-x64-${ORT_VERSION}.tgz" \
 && printf '%s  %s\n' "$PDFIUM_SHA256" /tmp/pdfium.tgz "$ORT_SHA256" /tmp/ort.tgz | sha256sum -c - \
 && tar -xzf /tmp/pdfium.tgz -C /opt/pdfium \
 && tar -xzf /tmp/ort.tgz -C /opt \
 && rm /tmp/pdfium.tgz /tmp/ort.tgz \
 && mv "/opt/onnxruntime-linux-x64-${ORT_VERSION}" /opt/onnxruntime \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --create-home stl && mkdir -p /data && chown stl /data
COPY --from=build /src/rust/target/release/stl-server /usr/local/bin/stl-server
COPY --from=web /web/dist /usr/local/bin/web
ENV STL_DATA_DIR=/data \
    STL_BIND=0.0.0.0:8770 \
    STL_PDFIUM_PATH=/opt/pdfium/lib/libpdfium.so \
    ORT_DYLIB_PATH=/opt/onnxruntime/lib/libonnxruntime.so
USER stl
VOLUME /data
EXPOSE 8770
ENTRYPOINT ["stl-server"]
