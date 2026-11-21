// The filter pane's Path section right-click menu (editors): crawl or rescan a
// folder, or crawl it with a path pattern kept for it.
import { Button, Group, Menu, Modal, Stack, Text, TextInput } from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap } from "../api/client";
import type { JobRequest } from "../jobs/jobs";
import { AiPatternButton } from "./AiPatternButton";
import { PatternSample } from "./PatternSample";
import { folderPattern } from "./patterns";

export type FolderMenuAt = { x: number; y: number; folder: string };

export function FolderMenu({ at, onClose, queueJob }: {
  at: FolderMenuAt | null;
  onClose: () => void;
  queueJob: (body: JobRequest, what: string) => Promise<void>;
}) {
  const libraries = useQuery({ queryKey: ["libraries"], queryFn: async () => unwrap(await api.GET("/api/libraries")) });
  // *Crawl with a pattern…*: the folder, and the pattern being typed.
  const [patternCrawl, setPatternCrawl] = useState<{ folder: string; pattern: string } | null>(null);
  return (
    <>
      <Menu opened={at != null} onChange={(o) => !o && onClose()} position="bottom-start" withinPortal>
        <Menu.Target>
          <div style={{ position: "fixed", left: at?.x ?? 0, top: at?.y ?? 0, width: 0, height: 0 }} />
        </Menu.Target>
        <Menu.Dropdown data-testid="folder-menu">
          <Menu.Label maw={320} style={{ overflowWrap: "anywhere" }}>{at?.folder}</Menu.Label>
          <Menu.Item
            leftSection={<IconRefresh size={14} />}
            onClick={() => at && queueJob({ kind: "crawl", folder: at.folder, in_library: true }, "Crawl")}
          >
            Crawl this folder (new and changed archives)
          </Menu.Item>
          <Menu.Item
            leftSection={<IconRefresh size={14} />}
            onClick={() => at && queueJob({ kind: "crawl", folder: at.folder, in_library: true, force_rescan: true }, "Rescan")}
          >
            Rescan this folder (read every archive again)
          </Menu.Item>
          <Menu.Item
            leftSection={<IconRefresh size={14} />}
            onClick={() => at && setPatternCrawl({ folder: at.folder, pattern: folderPattern(at.folder, libraries.data ?? []) })}
          >
            Crawl with a pattern…
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>
      <Modal opened={patternCrawl != null} onClose={() => setPatternCrawl(null)} title="Crawl with a pattern">
        {patternCrawl && (
          <Stack>
            <Text size="sm" style={{ overflowWrap: "anywhere" }}>{patternCrawl.folder}</Text>
            <TextInput
              label="Path pattern"
              description="Kept for the archives directly in this folder (its sub-folders keep their own, or the library's): later crawls and rescans use it. Read from the library folder's root; empty goes back to the library's pattern."
              value={patternCrawl.pattern}
              onChange={(e) => setPatternCrawl({ ...patternCrawl, pattern: e.currentTarget.value })}
              data-autofocus
            />
            <AiPatternButton folder={patternCrawl.folder} onPattern={(pattern) => setPatternCrawl((c) => c && { ...c, pattern })} />
            <PatternSample folder={patternCrawl.folder} pattern={patternCrawl.pattern} />
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setPatternCrawl(null)}>Cancel</Button>
              <Button
                onClick={async () => {
                  const c = patternCrawl;
                  setPatternCrawl(null);
                  await queueJob({ kind: "crawl", folder: c.folder, in_library: true, pattern: c.pattern.trim() }, "Crawl");
                }}
              >
                Start
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>
    </>
  );
}
