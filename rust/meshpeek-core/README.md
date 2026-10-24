# meshpeek-core, meshpeek-render (vendored)

Copies of MeshPeek's file loaders (`meshpeek-core`) and wgpu renderer (`meshpeek-render`), from
MeshPeek 0.5.6 (<https://github.com/mankoc/meshpeek>, commit `e2138f2`). The server loads models
from archives with `meshpeek-core`; the web client's model viewer (`../stl-viewer`, WebAssembly)
draws them with `meshpeek-render`.

Changes from MeshPeek (carry them over when syncing a newer MeshPeek):

- `meshpeek-core`: file loading (`io`) is behind the default `io` feature, so the web viewer
  builds without it; `zip` 9 with deflate only (the archivist's version); new `wire` module (the
  scene as bytes, for the server to send and the viewer to read).
- `meshpeek-render`: depends on `meshpeek-core` without `io`.
- Tests that need MeshPeek's `samples/` (golden, render) are not copied.
- `meshpeek-core/src/lib.rs` and `meshpeek-render/src/lib.rs` allow the clippy lints of newer toolchains that MeshPeek's code
  trips (`chunks_exact_to_as_chunks`, `collapsible_if`, `manual_is_multiple_of`), rather than
  rewriting it.
