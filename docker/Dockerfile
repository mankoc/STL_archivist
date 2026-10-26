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

# At least the highest rust-version of the locked dependencies (sevenz-rust2 0.24: 1.93).
ARG RUST_VERSION=1.93
FROM rust:${RUST_VERSION}-trixie AS build
WORKDIR /src
COPY rust/ rust/
RUN cd rust && cargo build --release -p stl-server

# The web client (its API types are committed, so no Rust is needed here).
FROM node:24-trixie-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY web/ ./
RUN npm run build

FROM debian:trixie-slim
ARG PDFIUM_BUILD=7881
ARG ORT_VERSION=1.22.0
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && mkdir -p /opt/pdfium \
 && curl -fsSL "https://github.com/bblanchon/pdfium-binaries/releases/download/chromium/${PDFIUM_BUILD}/pdfium-linux-x64.tgz" \
    | tar -xz -C /opt/pdfium \
 && curl -fsSL "https://github.com/microsoft/onnxruntime/releases/download/v${ORT_VERSION}/onnxruntime-linux-x64-${ORT_VERSION}.tgz" \
    | tar -xz -C /opt \
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
