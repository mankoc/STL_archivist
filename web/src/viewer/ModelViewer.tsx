// The model viewer: a model's archive, its model files on the left as a folder
// tree (read from the archive now) and the chosen ones drawn together by
// MeshPeek's renderer. Clicking a file shows it alone; its checkbox (or
// Ctrl-click) adds it to the view; a folder's checkbox shows all its files;
// ↑/↓ step through the files (in the tree: Enter shows the focused file
// alone, Space adds or takes it away). Right-click a file for its colour, material,
// wireframe and centring (MeshPeek's options), or a folder to show its files.
// In the desktop app, with MeshPeek installed and the archive reachable from
// this computer, "Open in MeshPeek" opens a file there.
import {
  ActionIcon, Alert, Box, Center, Checkbox, ColorSwatch, getTreeExpandedState, Group, Loader, Menu, Modal, ScrollArea,
  Text, Tooltip, Tree, useTree,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconAlertTriangle, IconCheck, IconChevronRight, IconExternalLink, IconFocus2, IconLayoutBoardSplit, IconPalette, IconPolygon,
} from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, unwrap } from "../api/client";
import { type FileTree, fileTree, foldersOf, treeFiles } from "../browser/fileTree";
import { clampWidth } from "../browser/paneWidth";
import { inDesktopShell, meshpeekState, openInMeshPeek } from "../desktop";
import { formatSize } from "../format";
import {
  COLORS, currentOf, type FileStyle, folderState, MATERIALS, restyle, stepFile, styleOf, toggleFile, toggleFiles,
} from "./state";
import { type FileStatus, ViewerCanvas } from "./ViewerCanvas";

const notify = (e: unknown) => notifications.show({ color: "red", message: String(e) });
const leaf = (name: string) => name.split("/").pop() ?? name;

export function ModelViewer({ id, title, file, clientPath, onClose }: {
  id: number;
  title: string;
  /** The model file to show first; the archive's first one when null. */
  file: string | null;
  /** The archive's path on this computer, when it is reachable from here. */
  clientPath: string | null;
  onClose: () => void;
}) {
  const files = useQuery({
    queryKey: ["model-viewable", id],
    queryFn: async () => unwrap(await api.GET("/api/models/{id}/viewable", { params: { path: { id } } })),
  });
  const meshpeek = useQuery({ queryKey: ["meshpeek"], queryFn: meshpeekState, enabled: inDesktopShell() });
  const tree = useMemo(() => (files.data ? fileTree(files.data) : null), [files.data]);
  const order = useMemo(() => (tree ? treeFiles(tree) : []), [tree]);
  const sizes = useMemo(() => Object.fromEntries((files.data ?? []).map((f) => [f.name, f.size])), [files.data]);

  const [shown, setShown] = useState<string[]>(file ? [file] : []);
  // The file last picked; the title, ↑/↓ and "Open in MeshPeek" go from it while it is shown.
  const [picked, setPicked] = useState<string | null>(file);
  const current = currentOf(shown, picked);
  // Without a file to start with, the archive's first one (once: taking every file away leaves the view empty).
  const started = useRef(file != null);
  useEffect(() => {
    if (started.current || !order.length) return;
    started.current = true;
    setShown([order[0]]);
    setPicked(order[0]);
  }, [order]);

  const [styles, setStyles] = useState<Record<string, FileStyle>>({});
  const [status, setStatus] = useState<Record<string, FileStatus>>({});
  const onStatus = useCallback((name: string, s: FileStatus | null) => {
    setStatus((m) => {
      const next = { ...m };
      if (s) next[name] = s;
      else delete next[name];
      return next;
    });
  }, []);
  const [colorEach, setColorEach] = useState(false);
  const [plates, setPlates] = useState(true);
  const [focus, setFocus] = useState<{ name: string; n: number } | null>(null);
  const [width, setWidth] = useState(320);

  const showOnly = (name: string) => {
    setShown([name]);
    setPicked(name);
  };
  const toggle = (name: string) => {
    setShown((s) => toggleFile(s, name));
    setPicked(name);
  };
  const allWire = shown.length > 0 && shown.every((n) => styleOf(styles, n).wire);
  const toggleWireAll = () => setStyles((s) => restyle(s, shown, { wire: !allWire }));

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      // In the tree the arrows move its focus (Enter then shows the focused file).
      if ((e.target as HTMLElement | null)?.closest("input, textarea, [role=menu], [role=tree]")) return;
      const next = stepFile(order, current, e.key === "ArrowDown" ? 1 : -1);
      if (next) {
        e.preventDefault();
        setShown([next]);
        setPicked(next);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [order, current]);

  const startResize = (e: React.PointerEvent) => {
    e.preventDefault();
    const x0 = e.clientX;
    const w0 = width;
    const move = (ev: PointerEvent) => setWidth(clampWidth(w0 + ev.clientX - x0, window.innerWidth, 0.5));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const openHere = (name: string) => openInMeshPeek(clientPath!, name).catch(notify);
  const canOpen = Boolean(clientPath && meshpeek.data?.program);
  const folder = current && current.includes("/") ? current.slice(0, current.lastIndexOf("/")) : null;

  return (
    <Modal
      opened
      onClose={onClose}
      fullScreen
      padding="xs"
      title={
        <Group gap="sm" wrap="nowrap">
          <Text fw={600} truncate>{title}</Text>
          {current && (
            <Text size="sm" truncate>
              {leaf(current)}
              {folder && <Text span size="sm" c="dimmed"> — {folder}</Text>}
              {shown.length > 1 && <Text span size="sm" c="dimmed"> (+{shown.length - 1} more shown)</Text>}
            </Text>
          )}
          {current && canOpen && (
            <ActionIconButton label="Open in MeshPeek" onClick={() => openHere(current)}>
              <IconExternalLink size={14} />
            </ActionIconButton>
          )}
        </Group>
      }
    >
      <Group wrap="nowrap" gap={0} align="stretch" h="calc(100vh - 70px)" data-testid="model-viewer">
        {tree && order.length > 1 && (
          <>
            <ScrollArea w={width} style={{ flexShrink: 0 }}>
              <FileTreePane
                tree={tree}
                order={order}
                current={current}
                shown={shown}
                status={status}
                styles={styles}
                onShowOnly={showOnly}
                onToggle={toggle}
                onToggleFolder={(names) => setShown((s) => toggleFiles(s, names))}
                onShowFolderOnly={(names) => {
                  setShown(names);
                  setPicked(names[0] ?? null);
                }}
                onStyle={(names, patch) => setStyles((s) => restyle(s, names, patch))}
                onFocus={(name) => setFocus({ name, n: Date.now() })}
                onOpen={canOpen ? openHere : null}
              />
            </ScrollArea>
            <Box w={6} style={{ cursor: "col-resize", flexShrink: 0 }} onPointerDown={startResize}
              onDoubleClick={() => setWidth(320)} aria-label="Resize the file list" />
          </>
        )}
        <Box style={{ flex: 1, minWidth: 0 }}>
          {files.isPending && !shown.length ? (
            <Center h="100%"><Loader /></Center>
          ) : files.isError && !shown.length ? (
            <Center h="100%"><Alert color="red">{files.error.message}</Alert></Center>
          ) : order.length || shown.length ? (
            <ViewerCanvas
              id={id}
              files={shown}
              sizes={sizes}
              styles={styles}
              colorEach={colorEach}
              plates={plates}
              focus={focus}
              onStatus={onStatus}
              onWireframeKey={toggleWireAll}
              tools={
                <>
                  <Toggle label="Wireframe (W)" on={allWire} onClick={toggleWireAll}><IconPolygon size={16} /></Toggle>
                  <Toggle label="Colour each model" on={colorEach} onClick={() => setColorEach((c) => !c)}><IconPalette size={16} /></Toggle>
                  <Toggle label="Build plate" on={plates} onClick={() => setPlates((p) => !p)}><IconLayoutBoardSplit size={16} /></Toggle>
                </>
              }
            />
          ) : (
            <Center h="100%"><Text c="dimmed">No model files the viewer can show in this archive.</Text></Center>
          )}
        </Box>
      </Group>
    </Modal>
  );
}

function ActionIconButton({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <Tooltip label={label}>
      <ActionIcon variant="light" aria-label={label} onClick={onClick}>
        {children}
      </ActionIcon>
    </Tooltip>
  );
}

function Toggle({ label, on, onClick, children }: { label: string; on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <Tooltip label={label}>
      <ActionIcon variant={on ? "filled" : "default"} aria-label={label} aria-pressed={on} onClick={onClick}>
        {children}
      </ActionIcon>
    </Tooltip>
  );
}

type Patch = Partial<FileStyle>;

/** The file tree with checkboxes, each file's state, and right-click menus. */
function FileTreePane({ tree, order, current, shown, status, styles, onShowOnly, onToggle, onToggleFolder, onShowFolderOnly, onStyle, onFocus, onOpen }: {
  tree: FileTree;
  order: string[];
  current: string | null;
  shown: string[];
  status: Record<string, FileStatus>;
  styles: Record<string, FileStyle>;
  onShowOnly: (name: string) => void;
  onToggle: (name: string) => void;
  onToggleFolder: (names: string[]) => void;
  onShowFolderOnly: (names: string[]) => void;
  onStyle: (names: string[], patch: Patch) => void;
  onFocus: (name: string) => void;
  onOpen: ((name: string) => void) | null;
}) {
  // Small archives open fully; large ones at the first file's folder.
  const controller = useTree({
    initialExpandedState: getTreeExpandedState(tree.data, order.length <= 40 ? "*" : current ? foldersOf(tree, current) : []),
  });
  useEffect(() => {
    if (current) for (const f of foldersOf(tree, current)) controller.expand(f);
    // (not on the controller: it is a new object each render)
  }, [tree, current]);
  const [menu, setMenu] = useState<{ value: string; x: number; y: number } | null>(null);
  const menuInfo = menu ? tree.info.get(menu.value) : undefined;

  // Enter shows the focused file alone, Space adds it to the view or takes it away (Mantine's tree moves the focus).
  const onTreeKey = (e: React.KeyboardEvent) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const value = (e.target as HTMLElement).closest("[role=treeitem]")?.getAttribute("data-value");
    const info = value != null ? tree.info.get(value) : undefined;
    if (info?.kind !== "file") return;
    e.preventDefault();
    e.stopPropagation();
    if (e.key === "Enter") onShowOnly(info.name!);
    else onToggle(info.name!);
  };

  return (
    <div onKeyDownCapture={onTreeKey}>
      <Tree
        data={tree.data}
        tree={controller}
        levelOffset={16}
        renderNode={({ node, expanded, hasChildren, elementProps }) => {
          const info = tree.info.get(node.value)!;
          const onContextMenu = (e: React.MouseEvent) => {
            e.preventDefault();
            setMenu({ value: node.value, x: e.clientX, y: e.clientY });
          };
          if (info.kind === "folder") {
            const state = folderState(shown, info.files!);
            return (
              <Group gap={4} wrap="nowrap" {...elementProps} onContextMenu={onContextMenu} style={{ ...elementProps.style, minHeight: 26 }}>
                <IconChevronRight size={14} style={{ flexShrink: 0, transform: expanded && hasChildren ? "rotate(90deg)" : undefined }} />
                <Checkbox size="xs" checked={state === "all"} indeterminate={state === "some"} aria-label={`Show ${node.label}`}
                  onClick={(e) => e.stopPropagation()} onChange={() => onToggleFolder(info.files!)} />
                <Text size="xs" fw={500} truncate style={{ flex: 1 }} title={node.value}>{node.label}</Text>
                <Text size="xs" c="dimmed">{info.files!.length}</Text>
              </Group>
            );
          }
          const name = info.name!;
          const s = status[name];
          const color = styleOf(styles, name).color;
          return (
            <Group
              gap={4}
              wrap="nowrap"
              {...elementProps}
              onClick={(e) => (e.ctrlKey || e.metaKey ? onToggle(name) : onShowOnly(name))}
              onContextMenu={onContextMenu}
              style={{
                ...elementProps.style,
                minHeight: 26,
                background: name === current ? "var(--mantine-primary-color-light)" : undefined,
                borderRadius: 4,
              }}
              data-testid="viewer-file"
            >
              <span style={{ width: 14, flexShrink: 0 }} />
              <Checkbox size="xs" checked={shown.includes(name)} aria-label={`Show ${name}`}
                onClick={(e) => e.stopPropagation()} onChange={() => onToggle(name)} />
              <Text size="xs" truncate style={{ flex: 1 }} title={name}>{node.label}</Text>
              {color && <ColorSwatch color={color} size={10} />}
              {s?.state === "loading" && <Loader size={10} />}
              {s?.state === "failed" && (
                <Tooltip label={s.error} multiline w={280}>
                  <IconAlertTriangle size={14} color="var(--mantine-color-red-6)" />
                </Tooltip>
              )}
              <Text size="xs" c="dimmed">{formatSize(info.size)}</Text>
            </Group>
          );
        }}
      />
      <Menu opened={menu != null} onChange={(o) => !o && setMenu(null)} position="bottom-start" withinPortal>
        <Menu.Target>
          <div style={{ position: "fixed", left: menu?.x ?? 0, top: menu?.y ?? 0, width: 0, height: 0 }} />
        </Menu.Target>
        <Menu.Dropdown>
          {menuInfo?.kind === "folder" && (
            <>
              <Menu.Item onClick={() => onShowFolderOnly(menuInfo.files!)}>Show only these</Menu.Item>
              <Menu.Item onClick={() => onToggleFolder(menuInfo.files!.filter((f) => !shown.includes(f)))}>Add all to the view</Menu.Item>
              <Menu.Item onClick={() => onToggleFolder(menuInfo.files!.filter((f) => shown.includes(f)))}>Take all out of the view</Menu.Item>
              <StyleItems names={menuInfo.files!} styles={styles} onStyle={onStyle} />
            </>
          )}
          {menuInfo?.kind === "file" && (
            <FileMenu name={menuInfo.name!} shown={shown.includes(menuInfo.name!)} styles={styles}
              onShowOnly={onShowOnly} onToggle={onToggle} onStyle={onStyle} onFocus={onFocus} onOpen={onOpen} />
          )}
        </Menu.Dropdown>
      </Menu>
    </div>
  );
}

function FileMenu({ name, shown, styles, onShowOnly, onToggle, onStyle, onFocus, onOpen }: {
  name: string;
  shown: boolean;
  styles: Record<string, FileStyle>;
  onShowOnly: (name: string) => void;
  onToggle: (name: string) => void;
  onStyle: (names: string[], patch: Patch) => void;
  onFocus: (name: string) => void;
  onOpen: ((name: string) => void) | null;
}) {
  return (
    <>
      <Menu.Label>{leaf(name)}</Menu.Label>
      <Menu.Item onClick={() => onShowOnly(name)}>Show only this</Menu.Item>
      <Menu.Item onClick={() => onToggle(name)}>{shown ? "Take out of the view" : "Add to the view"}</Menu.Item>
      {shown && (
        <Menu.Item leftSection={<IconFocus2 size={14} />} onClick={() => onFocus(name)}>
          Centre on it
        </Menu.Item>
      )}
      <StyleItems names={[name]} styles={styles} onStyle={onStyle} />
      {onOpen && (
        <>
          <Menu.Divider />
          <Menu.Item leftSection={<IconExternalLink size={14} />} onClick={() => onOpen(name)}>
            Open in MeshPeek
          </Menu.Item>
        </>
      )}
    </>
  );
}

/** Colour, material and wireframe for files (they apply once a file is shown, and stay when it is shown again). */
function StyleItems({ names, styles, onStyle }: {
  names: string[];
  styles: Record<string, FileStyle>;
  onStyle: (names: string[], patch: Patch) => void;
}) {
  const first = styleOf(styles, names[0] ?? "");
  const wire = names.length > 0 && names.every((n) => styleOf(styles, n).wire);
  const check = (on: boolean) => (on ? <IconCheck size={14} /> : <span style={{ width: 14 }} />);
  return (
    <>
      <Menu.Divider />
      <Menu.Label>Colour</Menu.Label>
      <Group gap={6} px="sm" pb={6} maw={220}>
        {COLORS.map((c) => (
          <ColorSwatch key={c} component="button" color={c} size={18} aria-label={`Colour ${c}`} style={{ cursor: "pointer" }}
            onClick={() => onStyle(names, { color: c })}>
            {first.color === c && <IconCheck size={12} color="white" />}
          </ColorSwatch>
        ))}
      </Group>
      <Menu.Item leftSection={check(first.color == null)} onClick={() => onStyle(names, { color: null })}>
        Default colour
      </Menu.Item>
      <Menu.Label>Material</Menu.Label>
      {MATERIALS.map((m) => (
        <Menu.Item key={m} leftSection={check(first.material === m)} onClick={() => onStyle(names, { material: m })}>
          {m}
        </Menu.Item>
      ))}
      <Menu.Divider />
      <Menu.Item leftSection={check(wire)} onClick={() => onStyle(names, { wire: !wire })}>
        Wireframe
      </Menu.Item>
    </>
  );
}
