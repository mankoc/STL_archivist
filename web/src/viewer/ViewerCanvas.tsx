// One model file drawn by MeshPeek's renderer (rust/stl-viewer, WebAssembly):
// the server loads the file from the archive (`GET /api/models/{id}/view`),
// the canvas shows it with MeshPeek's mouse and keyboard controls (input.ts).
// The WebAssembly is fetched the first time a viewer opens.
import { ActionIcon, Alert, Box, Center, Group, Loader, Menu, Text, Tooltip, useComputedColorScheme } from "@mantine/core";
import { IconCube, IconFocusCentered } from "@tabler/icons-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, unwrap } from "../api/client";
import { dragAction, dragZoom, keyAction, orbitDegrees, VIEWS, wheelZoom } from "./input";
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

interface Shown {
  objects: number;
  triangles: number;
  skipped: string[];
}

export function ViewerCanvas({ id, name }: { id: number; name: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewerRef = useRef<Viewer | null>(null);
  // WebGPU first; a canvas it failed on gets replaced by one for WebGL2.
  const [webgl, setWebgl] = useState(false);
  const [backend, setBackend] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [shown, setShown] = useState<Shown | null>(null);
  const dark = useComputedColorScheme("light") === "dark";

  const frame = useRef(0);
  const draw = useCallback(() => {
    if (frame.current) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = 0;
      try {
        viewerRef.current?.render();
      } catch (e) {
        setFailed(message(e));
      }
    });
  }, []);

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
    return () => {
      cancelled = true;
      cancelAnimationFrame(frame.current);
      frame.current = 0;
      viewerRef.current?.free();
      viewerRef.current = null;
      setBackend(null);
    };
  }, [webgl]);

  const ready = backend != null;

  useEffect(() => {
    if (!ready) return;
    viewerRef.current!.set_dark(dark);
    draw();
  }, [ready, dark, draw]);

  useEffect(() => {
    if (!ready) return;
    const ctl = new AbortController();
    setLoading(true);
    setFailed(null);
    setShown(null);
    (async () => {
      const data = unwrap(
        await api.GET("/api/models/{id}/view", {
          params: { path: { id }, query: { name } },
          parseAs: "arrayBuffer",
          signal: ctl.signal,
        }),
      ) as ArrayBuffer;
      if (ctl.signal.aborted || !viewerRef.current) return;
      const loaded = viewerRef.current.load(new Uint8Array(data));
      setShown({ objects: loaded.objects, triangles: loaded.triangles, skipped: loaded.skipped });
      loaded.free();
      draw();
    })()
      .catch((e) => !ctl.signal.aborted && setFailed(message(e)))
      .finally(() => !ctl.signal.aborted && setLoading(false));
    return () => ctl.abort();
  }, [ready, id, name, draw]);

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
  const act = (action: ReturnType<typeof keyAction>) => {
    const v = viewerRef.current;
    if (!v || !action) return;
    if (action === "fit") v.fit();
    else v.show_view(action);
    draw();
  };

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
        </Group>
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
      {shown && (
        <Box pos="absolute" bottom={8} left={8} right={8} style={{ pointerEvents: "none" }}>
          {shown.skipped.length > 0 && (
            <Alert color="yellow" p={6} mb={4} maw={520}>
              <Text size="xs">Not shown: {shown.skipped.join("; ")}</Text>
            </Alert>
          )}
          <Text size="xs" c="dimmed" px={6} py={2} display="inline-block"
            style={{ borderRadius: 6, background: "color-mix(in srgb, var(--mantine-color-body) 70%, transparent)" }}>
            {shown.objects} object{shown.objects === 1 ? "" : "s"} · {shown.triangles.toLocaleString()} triangles · {backend} ·
            left drag orbits, middle or Shift drag pans, wheel or right drag zooms
          </Text>
        </Box>
      )}
    </Box>
  );
}
