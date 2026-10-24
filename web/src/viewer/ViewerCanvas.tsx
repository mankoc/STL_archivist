// The model files drawn by MeshPeek's renderer (rust/stl-viewer, WebAssembly):
// the server loads each file from the archive (`GET /api/models/{id}/view`),
// the canvas shows them together with MeshPeek's mouse and keyboard controls
// (input.ts) and a view cube (viewCube.ts: click a face for that view, drag
// it to orbit). Files are added and removed as
// `files` changes; each is drawn as `styles` says. The WebAssembly is fetched
// the first time a viewer opens.
import { ActionIcon, Alert, Box, Center, Group, Loader, Menu, Text, Tooltip, useComputedColorScheme } from "@mantine/core";
import { IconCube, IconFocusCentered } from "@tabler/icons-react";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { api, unwrap } from "../api/client";
import { dragAction, dragZoom, keyAction, orbitDegrees, type ViewName, VIEWS, wheelZoom } from "./input";
import { type FileStyle, styleOf } from "./state";
import { cubeFaces, isClick } from "./viewCube";
import type { Viewer } from "./wasm/stl_viewer";

type Wasm = typeof import("./wasm/stl_viewer");
let wasm: Promise<Wasm> | null = null;

function loadWasm(): Promise<Wasm> {
  wasm ??= import("./wasm/stl_viewer").then(async (m) => {
    await m.default();
    return m;
  });
  return wasm;
}

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Size the canvas's image to its box in device pixels (WebGL2 allows at least 4096). */
function sizeCanvas(canvas: HTMLCanvasElement) {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.min(4096, Math.max(1, Math.round(canvas.clientWidth * dpr)));
  canvas.height = Math.min(4096, Math.max(1, Math.round(canvas.clientHeight * dpr)));
}

/** A file's state in the view, reported to the file list. */
export type FileStatus =
  | { state: "loading" }
  | { state: "shown"; objects: number; triangles: number; skipped: string[] }
  | { state: "failed"; error: string };

const CUBE = 34; // half-size in CSS pixels
const CUBE_BOX = CUBE * 2 + 24; // the cube's square, margin included
const AXIS_TINT = { x: "230, 70, 70", y: "90, 200, 90", z: "90, 130, 255" };

export function ViewerCanvas({ id, files, styles, colorEach, plates, focus, onStatus, onWireframeKey, tools }: {
  id: number;
  /** The files to show (member names). */
  files: string[];
  styles: Record<string, FileStyle>;
  colorEach: boolean;
  plates: boolean;
  /** Frame this file (a new `n` asks again). */
  focus: { name: string; n: number } | null;
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
  const [backend, setBackend] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const dark = useComputedColorScheme("light") === "dark";
  // Each file's request (while loading) or result, by name.
  const loaded = useRef(new Map<string, AbortController | FileStatus>());
  const [shown, setShown] = useState<Record<string, FileStatus>>({});
  const [added, setAdded] = useState(0); // counts additions, so styles are applied to new files
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
        setFailed(message(e));
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

  useEffect(() => {
    let cancelled = false;
    const canvas = canvasRef.current!;
    sizeCanvas(canvas);
    loadWasm()
      .then((m) => m.Viewer.create(canvas, webgl))
      .then(
        (v) => {
          if (cancelled) return v.free();
          viewerRef.current = v;
          setBackend(v.backend);
        },
        (e) => {
          if (cancelled) return;
          if (!webgl) setWebgl(true);
          else setFailed(`This browser cannot show 3D models: ${message(e)}`);
        },
      );
    const files = loaded.current;
    return () => {
      cancelled = true;
      cancelAnimationFrame(frame.current);
      frame.current = 0;
      for (const [name, entry] of files) {
        if (entry instanceof AbortController) entry.abort();
        onStatus(name, null);
      }
      files.clear();
      setShown({});
      viewerRef.current?.free();
      viewerRef.current = null;
      setBackend(null);
    };
  }, [webgl, onStatus]);

  const ready = backend != null;

  useEffect(() => {
    if (!ready) return;
    viewerRef.current!.set_dark(dark);
    draw();
  }, [ready, dark, draw]);

  // Load the files asked for, drop the others.
  useEffect(() => {
    if (!ready) return;
    const v = viewerRef.current!;
    const wanted = new Set(files);
    for (const [name, entry] of loaded.current) {
      if (wanted.has(name)) continue;
      if (entry instanceof AbortController) entry.abort();
      else v.remove(name);
      loaded.current.delete(name);
      report(name, null);
    }
    draw();
    for (const name of files) {
      if (loaded.current.has(name)) continue;
      const ctl = new AbortController();
      loaded.current.set(name, ctl);
      report(name, { state: "loading" });
      (async () => {
        const data = unwrap(
          await api.GET("/api/models/{id}/view", { params: { path: { id }, query: { name } }, parseAs: "arrayBuffer", signal: ctl.signal }),
        ) as ArrayBuffer;
        if (ctl.signal.aborted || viewerRef.current !== v) return;
        const r = v.add(name, new Uint8Array(data));
        const status: FileStatus = { state: "shown", objects: r.objects, triangles: r.triangles, skipped: r.skipped };
        r.free();
        loaded.current.set(name, status);
        report(name, status);
        setAdded((n) => n + 1);
      })().catch((e) => {
        if (ctl.signal.aborted) return;
        const status: FileStatus = { state: "failed", error: message(e) };
        loaded.current.set(name, status);
        report(name, status);
      });
    }
  }, [ready, id, files, draw, report]);

  useEffect(() => {
    if (!ready) return;
    const v = viewerRef.current!;
    for (const [name, entry] of loaded.current) {
      if (entry instanceof AbortController || entry.state !== "shown") continue;
      const s = styleOf(styles, name);
      v.set_color(name, s.color);
      v.set_material(name, s.material);
      v.set_wireframe(name, s.wire);
    }
    v.set_color_each(colorEach);
    v.set_show_plates(plates);
    draw();
  }, [ready, styles, colorEach, plates, added, draw]);

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
    const dpr = window.devicePixelRatio || 1;
    const [dx, dy] = [(e.clientX - last.current.x) * dpr, (e.clientY - last.current.y) * dpr];
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

  const statuses = Object.values(shown);
  const done = statuses.flatMap((s) => (s.state === "shown" ? [s] : []));
  const loading = statuses.some((s) => s.state === "loading");
  const skipped = done.flatMap((s) => s.skipped);
  const objects = done.reduce((n, s) => n + s.objects, 0);
  const triangles = done.reduce((n, s) => n + s.triangles, 0);

  return (
    <Box pos="relative" h="100%" w="100%" data-testid="model-canvas">
      <canvas
        key={String(webgl)}
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
          <Alert color="red" maw={520}>{failed}</Alert>
        </Center>
      )}
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
