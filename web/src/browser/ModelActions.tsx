// The actions on models, the same in the toolbar's Actions menu (the selected
// models) and the right-click menu of a row or image: editing, metadata,
// archive files and removal for editors; for one model also its archive
// (download, open, copy path), which viewers get too.
import { Menu } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconAlertTriangleOff,
  IconCopy,
  IconDownload,
  IconEdit,
  IconExternalLink,
  IconFileX,
  IconFileZip,
  IconFolderOpen,
  IconFolders,
  IconPhoto,
  IconRefresh,
  IconSparkles,
  IconTrash,
} from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";

import { api, unwrap } from "../api/client";
import { inDesktopShell, meshpeekState, openFolder, openInMeshPeek, openInPacker, packerState } from "../desktop";
import type { JobRequest } from "../jobs/jobs";

export interface ModelActionHandlers {
  edit: (id: number) => void;
  massEdit: () => void;
  aiTagger: () => void;
  clearWarnings: (ids: number[]) => void;
  queueJob: (body: JobRequest, what: string) => void;
  sortFiles: () => void;
  deleteFiles: () => void;
  remove: () => void;
}

const notify = (err: unknown) => notifications.show({ color: "red", message: (err as Error).message });

/**
 * Menu items for `ids`. The toolbar's (`where: "toolbar"`) leave out editing, its own
 * button; a right-click menu's (`"menu"`) start with Edit… (one model) or Mass edit…
 * and, for one model, its archive.
 */
export function ModelActionItems({ ids, where, editor, on }: { ids: number[]; where: "toolbar" | "menu"; editor: boolean; on: ModelActionHandlers }) {
  const one = ids.length === 1 ? ids[0] : null;
  const menu = where === "menu";
  return (
    <>
      {menu && editor && (
        <Menu.Item leftSection={<IconEdit size={14} />} onClick={() => (one != null ? on.edit(one) : on.massEdit())}>
          {one != null ? "Edit…" : `Mass edit ${ids.length} models…`}
        </Menu.Item>
      )}
      {menu && one != null && <ArchiveItems id={one} />}
      {editor && (
        <>
          <Menu.Label>Metadata</Menu.Label>
          <Menu.Item leftSection={<IconSparkles size={14} />} onClick={on.aiTagger}>AI tagger…</Menu.Item>
          <Menu.Item leftSection={<IconAlertTriangleOff size={14} />} onClick={() => on.clearWarnings(ids)}>Clear warnings</Menu.Item>
          <Menu.Label>Archive files</Menu.Label>
          <Menu.Item leftSection={<IconRefresh size={14} />} onClick={() => on.queueJob({ kind: "rescan", ids }, "Rescan")}>
            {ids.length > 1 ? `Rescan ${ids.length} archives` : "Rescan"}
          </Menu.Item>
          <Menu.Item leftSection={<IconPhoto size={14} />} onClick={() => on.queueJob({ kind: "generate_thumbnails", ids }, "Thumbnail generation")}>
            Generate thumbnails
          </Menu.Item>
          <Menu.Item leftSection={<IconFolders size={14} />} onClick={on.sortFiles}>Sort files…</Menu.Item>
          <Menu.Item color="red" leftSection={<IconFileX size={14} />} onClick={on.deleteFiles}>Delete files…</Menu.Item>
          <Menu.Divider />
          <Menu.Item color="red" leftSection={<IconTrash size={14} />} onClick={on.remove} data-testid="remove-models">
            Remove from library…
          </Menu.Item>
        </>
      )}
    </>
  );
}

/** One model's archive: download it, open it on this computer (desktop app), copy its path. */
function ArchiveItems({ id }: { id: number }) {
  // The detail pane's queries: the right-clicked model is the one it shows.
  const detail = useQuery({
    queryKey: ["model", id],
    queryFn: async () => unwrap(await api.GET("/api/models/{id}", { params: { path: { id } } })),
  });
  const meshpeek = useQuery({ queryKey: ["meshpeek"], queryFn: meshpeekState, enabled: inDesktopShell() });
  const packer = useQuery({ queryKey: ["packer"], queryFn: packerState, enabled: inDesktopShell() });
  const path = detail.data?.client_path;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(path!);
      notifications.show({ message: "Path copied" });
    } catch (err) {
      notify(err);
    }
  };
  return (
    <>
      <Menu.Label>This archive</Menu.Label>
      <Menu.Item component="a" href={`/api/models/${id}/archive`} download leftSection={<IconDownload size={14} />}>
        Download
      </Menu.Item>
      {path && inDesktopShell() && (
        <Menu.Item leftSection={<IconFolderOpen size={14} />} onClick={() => openFolder(path).catch(notify)}>Open folder</Menu.Item>
      )}
      {path && meshpeek.data?.program && (
        <Menu.Item leftSection={<IconExternalLink size={14} />} onClick={() => openInMeshPeek(path).catch(notify)}>Open in MeshPeek</Menu.Item>
      )}
      {path && packer.data?.program && (
        <Menu.Item leftSection={<IconFileZip size={14} />} onClick={() => openInPacker(path).catch(notify)}>Open in packer</Menu.Item>
      )}
      {path && (
        <Menu.Item leftSection={<IconCopy size={14} />} onClick={copy}>Copy path</Menu.Item>
      )}
    </>
  );
}
