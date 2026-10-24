// The model files drawn by MeshPeek's renderer (rust/stl-viewer, WebAssembly):
// the server loads them from the archive in batches (`POST
// /api/models/{id}/view`, loading.ts), the canvas shows them together with
// MeshPeek's mouse and keyboard controls (input.ts) and a view cube
// (viewCube.ts: click a face for that view, drag it to orbit). Files leaving
// `files` are hidden, not dropped, so showing them again is instant (the
// oldest hidden ones are forgotten); each is drawn as `styles` says, restyled
// only when its style changes. The WebAssembly is fetched the first time a
// viewer opens (again after a failed download). `ref` gives the view as an
// image (`snapshot`: the canvas alone, without the toolbar or view cube).
import { ActionIcon, Alert, Box, Button, Center, Group, Loader, Menu, Modal, Table, Text, Tooltip, useComputedColorScheme } from "@mantine/core";
import { IconCube, IconFocusCentered } from "@tabler/icons-react";
import { type ReactNode, type Ref, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";

import { api, unwrap } from "../api/client";
import { infoRows } from "./info";
import { canvasSize, dragAction, dragZoom, keyAction, orbitDegrees, type ViewName, VIEWS, wheelZoom } from "./input";
import { evictHidden, planBatches, splitBundle } from "./loading";
import { type FileStyle, styleOf } from "./state";
import { cubeFaces, isClick } from "./viewCube";
import type { Viewer } from "./wasm/stl_viewer";

type Wasm = typeof import("./wasm/stl_viewer");
let wasm: Promise<Wasm> | null = null;

/** The WebAssembly module, downloaded once; a failed download is tried again next time. */
function loadWasm(): Promise<Wasm> {
  wasm ??= import("./wasm/stl_viewer")
    .then(async (m) => {
      await m.default();
      return m;
    })
    .catch((e) => {
      wasm = null;
      throw e;
    });
  return wasm;
}

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

function sizeCanvas(canvas: HTMLCanvasElement) {
  [canvas.width, canvas.height] = canvasSize(canvas.clientWidth, canvas.clientHeight, window.devicePixelRatio);
}

/** What a loaded file holds. */
interface Shown {
  objects: number;
  triangles: number;
  skipped: string[];
}

/** A file's state in the view, reported to the file list. */
export type FileStatus = { state: "loading" } | ({ state: "shown" } & Shown) | { state: "failed"; error: string };

/** One request for files, aborted when none of them is wanted any more. */
interface Batch {
  ctl: AbortController;
  names: string[];
}

/** A file the viewer knows: being loaded, shown, hidden (loaded, kept to show again), or failed. */
type Entry =
  | { state: "loading"; batch: Batch }
  | { state: "shown" | "hidden"; info: Shown }
  | { state: "failed"; error: string };

/** What the page can ask of the canvas. */
export interface ViewerHandle {
  /** The view as drawn now, as a PNG at the canvas's size. */
  snapshot: () => Promise<Blob>;
}

const CUBE = 34; // half-size in CSS pixels
const CUBE_BOX = CUBE * 2 + 24; // the cube's square, margin included
const AXIS_TINT = { x: "230, 70, 70", y: "90, 200, 90", z: "90, 130, 255" };

export function ViewerCanvas({ ref, id, files, sizes, styles, colorEach, plates, focus, info, onInfoClose, onStatus, onWireframeKey, tools }: {
  ref?: Ref<ViewerHandle>;
  id: number;
  /** The files to show (member names). */
  files: string[];
  /** The files' sizes in the archive, for grouping requests. */
  sizes: Record<string, number | null | undefined>;
  styles: Record<string, FileStyle>;
  colorEach: boolean;
  plates: boolean;
  /** Frame this file (a new `n` asks again). */
  focus: { name: string; n: number } | null;
  /** The file whose information panel is open. */
  info: string | null;
  onInfoClose: () => void;
  /** A file's state changed; null: it is no longer shown. */
  onStatus: (name: string, status: FileStatus | null) => void;
  /** W was pressed. */
  onWireframeKey: () => void;
  /** More buttons for the toolbar. */
  tools?: ReactNode;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewerRef = useRef<Viewer | null>(null);
  // WebGPU first; a canvas it failed on gets replaced by one for WebGL2.
  const [webgl, setWebgl] = useState(false);
  // Counts "Try again" after a failed download (a new canvas each time).
  const [attempt, setAttempt] = useState(0);
  const [backend, setBackend] = useState<string | null>(null);
  const [failed, setFailed] = useState<{ message: string; retry: boolean } | null>(null);
  const dark = useComputedColorScheme("light") === "dark";
  const entries = useRef(new Map<string, Entry>());
  // Hidden files, the oldest first.
  const hidden = useRef<string[]>([]);
  // The style each file was last drawn with, so unchanged ones are left alone.
  const applied = useRef(new Map<string, string>());
  const stylesRef = useRef(styles);
  stylesRef.current = styles;
  const [shown, setShown] = useState<Record<string, FileStatus>>({});
  const [basis, setBasis] = useState<number[] | null>(null);

  const frame = useRef(0);
  const draw = useCallback(() => {
    if (frame.current) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = 0;
      const v = viewerRef.current;
      if (!v) return;
      try {
        v.render();
        setBasis(Array.from(v.camera_basis()));
      } catch (e) {
        setFailed({ message: message(e), retry: false });
      }
    });
  }, []);

  const report = useCallback(
    (name: string, status: FileStatus | null) => {
      setShown((m) => {
        const next = { ...m };
        if (status) next[name] = status;
        else delete next[name];
        return next;
      });
      onStatus(name, status);
    },
    [onStatus],
  );

  /** Draw the file as its style says, unless it already is. */
  const applyStyle = useCallback((name: string) => {
    const v = viewerRef.current;
    if (!v) return;
    const s = styleOf(stylesRef.current, name);
    const key = `${s.color}|${s.material}|${s.wire}`;
    if (applied.current.get(name) === key) return;
    applied.current.set(name, key);
    v.set_style(name, s.color, s.material, s.wire);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const canvas = canvasRef.current!;
    sizeCanvas(canvas);
    loadWasm().then(
      (m) =>
        m.Viewer.create(canvas, webgl).then(
          (v) => {
            if (cancelled) return v.free();
            viewerRef.current = v;
            setBackend(v.backend);
          },
          (e) => {
            if (cancelled) return;
            if (!webgl) setWebgl(true);
            else setFailed({ message: `This browser cannot show 3D models: ${message(e)}`, retry: false });
          },
        ),
      (e) => {
        if (!cancelled) setFailed({ message: `The 3D viewer could not be downloaded: ${message(e)}`, retry: true });
      },
    );
    const known = entries.current;
    const hiddenFiles = hidden;
    const styled = applied.current;
    return () => {
      cancelled = true;
      cancelAnimationFrame(frame.current);
      frame.current = 0;
      for (const [name, entry] of known) {
        if (entry.state === "loading") entry.batch.ctl.abort();
        if (entry.state !== "hidden") onStatus(name, null);
      }
      known.clear();
      hiddenFiles.current = [];
      styled.clear();
      setShown({});
      viewerRef.current?.free();
      viewerRef.current = null;
      setBackend(null);
    };
  }, [webgl, attempt, onStatus]);

  const ready = backend != null;

  useImperativeHandle(ref, () => ({
    snapshot: () =>
      new Promise<Blob>((resolve, reject) => {
        const v = viewerRef.current;
        const canvas = canvasRef.current;
        if (!v || !canvas) return reject(new Error("The viewer is not ready."));
        // Drawn and read in one task: a WebGL2 canvas may be cleared once the browser has shown it.
        v.render();
        canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("The view could not be read."))), "image/png");
      }),
  }), []);

  useEffect(() => {
    if (!ready) return;
    viewerRef.current!.set_dark(dark);
    draw();
  }, [ready, dark, draw]);

  // Hide the files no longer asked for, show again the hidden ones asked for, load the rest.
  useEffect(() => {
    if (!ready) return;
    const v = viewerRef.current!;
    const known = entries.current;
    const wanted = new Set(files);
    const fail = (name: string, error: string) => {
      known.set(name, { state: "failed", error });
      report(name, { state: "failed", error });
    };
    const loadingIn = (name: string, batch: Batch) => {
      const e = known.get(name);
      return e?.state === "loading" && e.batch === batch;
    };
    for (const [name, e] of known) {
      if (wanted.has(name) || e.state === "hidden") continue;
      if (e.state === "shown") {
        v.set_visible(name, false);
        known.set(name, { state: "hidden", info: e.info });
        hidden.current.push(name);
      } else {
        known.delete(name); // a failed file is tried again when it is asked for again
        if (e.state === "loading" && !e.batch.names.some((n) => loadingIn(n, e.batch))) e.batch.ctl.abort();
      }
      report(name, null);
    }
    const trianglesOf = (name: string) => {
      const e = known.get(name);
      return e && "info" in e ? e.info.triangles : 0;
    };
    const drop = evictHidden(hidden.current.filter((n) => !wanted.has(n)).map((name) => ({ name, triangles: trianglesOf(name) })));
    for (const name of drop) {
      v.remove(name);
      known.delete(name);
      applied.current.delete(name);
    }
    hidden.current = hidden.current.filter((n) => !drop.includes(n) && !wanted.has(n));
    for (const name of files) {
      const e = known.get(name);
      if (e?.state !== "hidden") continue;
      v.set_visible(name, true);
      known.set(name, { state: "shown", info: e.info });
      report(name, { state: "shown", ...e.info });
    }
    draw();

    const missing = files.filter((n) => !known.has(n));
    if (!missing.length) return;
    const batches: Batch[] = planBatches(missing, sizes).map((names) => ({ ctl: new AbortController(), names }));
    for (const batch of batches) {
      for (const name of batch.names) {
        known.set(name, { state: "loading", batch });
        report(name, { state: "loading" });
      }
    }
    // One batch after another: each answer is held in memory until its files are on the GPU.
    void (async () => {
      for (const batch of batches) {
        if (batch.ctl.signal.aborted) continue;
        try {
          const data = unwrap(
            await api.POST("/api/models/{id}/view", {
              params: { path: { id } },
              body: { names: batch.names },
              parseAs: "arrayBuffer",
              signal: batch.ctl.signal,
            }),
          ) as ArrayBuffer;
          if (viewerRef.current !== v) return;
          for (const f of splitBundle(data)) {
            if (!loadingIn(f.name, batch)) continue; // no longer wanted
            if ("error" in f) {
              fail(f.name, f.error);
              continue;
            }
            try {
              const r = v.add(f.name, f.scene);
              const info: Shown = { objects: r.objects, triangles: r.triangles, skipped: r.skipped };
              r.free();
              known.set(f.name, { state: "shown", info });
              applyStyle(f.name);
              report(f.name, { state: "shown", ...info });
            } catch (e) {
              fail(f.name, message(e));
            }
          }
          for (const name of batch.names) if (loadingIn(name, batch)) fail(name, "the server did not send it");
          draw();
        } catch (e) {
          if (batch.ctl.signal.aborted || viewerRef.current !== v) continue;
          for (const name of batch.names) if (loadingIn(name, batch)) fail(name, message(e));
        }
      }
    })();
  }, [ready, id, files, sizes, draw, report, applyStyle]);

  useEffect(() => {
    if (!ready) return;
    for (const [name, e] of entries.current) if (e.state === "shown" || e.state === "hidden") applyStyle(name);
    draw();
  }, [ready, styles, applyStyle, draw]);

  useEffect(() => {
    if (!ready) return;
    viewerRef.current!.set_color_each(colorEach);
    viewerRef.current!.set_show_plates(plates);
    draw();
  }, [ready, colorEach, plates, draw]);

  useEffect(() => {
    if (!ready || !focus) return;
    viewerRef.current!.focus(focus.name);
    draw();
  }, [ready, focus, draw]);

  useEffect(() => {
    if (!ready) return;
    const canvas = canvasRef.current!;
    const resized = new ResizeObserver(() => {
      sizeCanvas(canvas);
      viewerRef.current?.resize(canvas.width, canvas.height);
      draw();
    });
    resized.observe(canvas);
    // The page must not scroll while the wheel zooms, so not a (passive) React handler.
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      viewerRef.current?.zoom(wheelZoom(e.deltaY, e.deltaMode));
      draw();
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      resized.disconnect();
      canvas.removeEventListener("wheel", onWheel);
    };
  }, [ready, draw]);

  const last = useRef<{ x: number; y: number } | null>(null);
  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const v = viewerRef.current;
    if (!last.current || !v) return;
    // CSS pixels to canvas pixels, as the canvas is really sized (see canvasSize).
    const scale = e.currentTarget.width / Math.max(1, e.currentTarget.clientWidth);
    const [dx, dy] = [(e.clientX - last.current.x) * scale, (e.clientY - last.current.y) * scale];
    last.current = { x: e.clientX, y: e.clientY };
    const action = dragAction(e.buttons, e.shiftKey);
    if (action === "orbit") v.orbit(...orbitDegrees(dx, dy, e.currentTarget.width, e.currentTarget.height));
    else if (action === "pan") v.pan(dx, dy);
    else if (action === "zoom") v.zoom(dragZoom(dy));
    draw();
  };
  // A press on the view cube: where it started and last was, the face under it, whether it became a drag.
  const cubePress = useRef<{ x0: number; y0: number; x: number; y: number; view: ViewName | null; drag: boolean } | null>(null);
  const [cubeHover, setCubeHover] = useState<ViewName | null>(null);
  const [cubeDragging, setCubeDragging] = useState(false);
  const onCubeDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (e.button !== 0) return;
    e.preventDefault(); // no text selection
    e.currentTarget.setPointerCapture(e.pointerId);
    const view = (e.target as Element).closest("[data-view]")?.getAttribute("data-view") as ViewName | null;
    cubePress.current = { x0: e.clientX, y0: e.clientY, x: e.clientX, y: e.clientY, view, drag: false };
  };
  const onCubeMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const p = cubePress.current;
    const v = viewerRef.current;
    if (!p || !v) return;
    if (!p.drag) {
      if (isClick(e.clientX - p.x0, e.clientY - p.y0)) return;
      p.drag = true;
      setCubeDragging(true);
    }
    // The cube's width turns 200 degrees, as a full-width drag of the view does.
    v.orbit(...orbitDegrees(e.clientX - p.x, e.clientY - p.y, CUBE_BOX, CUBE_BOX));
    p.x = e.clientX;
    p.y = e.clientY;
    draw();
  };
  const onCubeUp = () => {
    const p = cubePress.current;
    cubePress.current = null;
    setCubeDragging(false);
    if (p && !p.drag && p.view) act(p.view);
  };

  const act = (action: ReturnType<typeof keyAction>) => {
    const v = viewerRef.current;
    if (!v || !action) return;
    if (action === "wireframe") return onWireframeKey();
    if (action === "fit") v.fit();
    else v.show_view(action);
    draw();
  };

  // The information panel's rows (read when it opens, or when the file finishes loading).
  const infoState = info ? shown[info]?.state : undefined;
  const panel = useMemo(() => {
    const v = viewerRef.current;
    const facts = info && infoState === "shown" ? v?.file_info(info) : undefined;
    if (!info || !facts) return null;
    const rows = infoRows(info, {
      objects: facts.objects,
      models: facts.models,
      triangles: facts.triangles,
      support_triangles: facts.support_triangles,
      size: facts.size,
      facts: facts.facts,
    });
    facts.free();
    return rows;
  }, [info, infoState]);

  const statuses = Object.values(shown);
  const done = statuses.flatMap((s) => (s.state === "shown" ? [s] : []));
  const loading = statuses.some((s) => s.state === "loading");
  const skipped = done.flatMap((s) => s.skipped);
  const objects = done.reduce((n, s) => n + s.objects, 0);
  const triangles = done.reduce((n, s) => n + s.triangles, 0);

  return (
    <Box pos="relative" h="100%" w="100%" data-testid="model-canvas">
      <canvas
        key={`${webgl}-${attempt}`}
        ref={canvasRef}
        tabIndex={0}
        style={{ width: "100%", height: "100%", display: "block", outline: "none", touchAction: "none" }}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId);
          e.currentTarget.focus();
          last.current = { x: e.clientX, y: e.clientY };
        }}
        onPointerMove={onPointerMove}
        onPointerUp={() => (last.current = null)}
        onPointerCancel={() => (last.current = null)}
        onMouseDown={(e) => e.button === 1 && e.preventDefault()} // no autoscroll on middle drag
        onContextMenu={(e) => e.preventDefault()}
        onKeyDown={(e) => act(keyAction(e.key))}
      />
      {ready && (
        <Group gap={4} pos="absolute" top={8} left={8}>
          <Menu position="bottom-start">
            <Menu.Target>
              <Tooltip label="Standard views (0–6)">
                <ActionIcon variant="default" aria-label="Standard views">
                  <IconCube size={16} />
                </ActionIcon>
              </Tooltip>
            </Menu.Target>
            <Menu.Dropdown>
              {VIEWS.map(([view, label], i) => (
                <Menu.Item key={view} onClick={() => act(view)} rightSection={<Text size="xs" c="dimmed">{i}</Text>}>
                  {label}
                </Menu.Item>
              ))}
            </Menu.Dropdown>
          </Menu>
          <Tooltip label="Fit all (F)">
            <ActionIcon variant="default" aria-label="Fit all" onClick={() => act("fit")}>
              <IconFocusCentered size={16} />
            </ActionIcon>
          </Tooltip>
          {tools}
        </Group>
      )}
      {ready && basis && (
        <svg
          width={CUBE_BOX}
          height={CUBE_BOX}
          viewBox={`${-CUBE_BOX / 2} ${-CUBE_BOX / 2} ${CUBE_BOX} ${CUBE_BOX}`}
          style={{ position: "absolute", top: 8, right: 8, overflow: "visible", touchAction: "none", cursor: cubeDragging ? "grabbing" : "grab" }}
          onPointerDown={onCubeDown}
          onPointerMove={onCubeMove}
          onPointerUp={onCubeUp}
          onPointerCancel={() => {
            cubePress.current = null;
            setCubeDragging(false);
          }}
          onWheel={(e) => {
            viewerRef.current?.zoom(wheelZoom(e.deltaY, e.deltaMode));
            draw();
          }}
          data-testid="view-cube"
        >
          <title>Click a face for that view; drag to turn the view</title>
          {cubeFaces(basis, CUBE / Math.sqrt(3)).map((f) => (
            <g
              key={f.view}
              data-view={f.view}
              style={{ cursor: cubeDragging ? "grabbing" : "pointer" }}
              onPointerEnter={() => setCubeHover(f.view)}
              onPointerLeave={() => setCubeHover((h) => (h === f.view ? null : h))}
            >
              <title>{`${f.label} view`}</title>
              <polygon
                points={f.points.map((p) => p.join(",")).join(" ")}
                fill={`rgba(${AXIS_TINT[f.axis]}, ${Math.min(1, 0.25 + 0.45 * f.facing + (cubeHover === f.view && !cubeDragging ? 0.3 : 0))})`}
                stroke={dark ? "rgba(255,255,255,0.6)" : "rgba(0,0,0,0.45)"}
                strokeWidth={1}
                strokeLinejoin="round"
              />
              {f.facing > 0.35 && (
                <text x={f.centre[0]} y={f.centre[1]} textAnchor="middle" dominantBaseline="central" fontSize={10}
                  fill={dark ? "#fff" : "#111"} style={{ pointerEvents: "none", userSelect: "none" }}>
                  {f.label}
                </text>
              )}
            </g>
          ))}
        </svg>
      )}
      {(loading || (!ready && !failed)) && (
        <Center pos="absolute" inset={0} style={{ pointerEvents: "none" }}>
          <Loader />
        </Center>
      )}
      {failed && (
        <Center pos="absolute" inset={0} p="md">
          <Alert color="red" maw={520}>
            {failed.message}
            {failed.retry && (
              <Button
                size="xs"
                variant="light"
                color="red"
                mt="xs"
                display="block"
                onClick={() => {
                  setFailed(null);
                  setAttempt((n) => n + 1);
                }}
              >
                Try again
              </Button>
            )}
          </Alert>
        </Center>
      )}
      <Modal opened={info != null} onClose={onInfoClose} title={`Information: ${info?.split("/").pop() ?? ""}`} size="md">
        {panel ? (
          <Table withRowBorders={false} verticalSpacing={3} fz="sm" data-testid="file-info">
            <Table.Tbody>
              {panel.map(([label, value], i) => (
                <Table.Tr key={i}>
                  <Table.Td c="dimmed" w={150} style={{ verticalAlign: "top" }}>{label}</Table.Td>
                  <Table.Td style={{ wordBreak: "break-all" }}>{value}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        ) : (
          <Text size="sm" c="dimmed">Show the file to see its information.</Text>
        )}
      </Modal>
      {done.length > 0 && (
        <Box pos="absolute" bottom={8} left={8} right={8} style={{ pointerEvents: "none" }}>
          {skipped.length > 0 && (
            <Alert color="yellow" p={6} mb={4} maw={520}>
              <Text size="xs">Not shown: {skipped.join("; ")}</Text>
            </Alert>
          )}
          <Text size="xs" c="dimmed" px={6} py={2} display="inline-block"
            style={{ borderRadius: 6, background: "color-mix(in srgb, var(--mantine-color-body) 70%, transparent)" }}>
            {done.length > 1 && `${done.length} files · `}
            {objects} object{objects === 1 ? "" : "s"} · {triangles.toLocaleString()} triangles · {backend} ·
            left drag orbits, middle or Shift drag pans, wheel or right drag zooms
          </Text>
        </Box>
      )}
    </Box>
  );
}
