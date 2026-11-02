// The buttons beside a path pattern: its tokens (click one to add it) and the
// patterns this user saved (`patterns.ts`). Sort files builds paths from a
// pattern; mass edit parses values out of paths with one.
import { ActionIcon, Button, Code, Menu, Popover, Table, Text, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconBookmark, IconDeviceFloppy, IconHelp, IconX } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "../api/client";
import { PARSE_PATTERNS, SAVED_PATTERNS, savedPatterns, TOKENS, withoutPattern, withPattern } from "./patterns";

export function PatternTools({ pattern, onChange, mode }: { pattern: string; onChange: (p: string) => void; mode: "sort" | "parse" }) {
  const queryClient = useQueryClient();
  const key = mode === "sort" ? SAVED_PATTERNS : PARSE_PATTERNS;
  const prefs = useQuery({ queryKey: ["me-settings"], queryFn: async () => unwrap(await api.GET("/api/me/settings")) as Record<string, unknown> });
  const saved = savedPatterns(prefs.data?.[key]);
  const store = async (list: string[]) => {
    try {
      unwrap(await api.PUT("/api/me/settings/{key}", { params: { path: { key } }, body: list as never }));
      await queryClient.invalidateQueries({ queryKey: ["me-settings"] });
    } catch (err) {
      notifications.show({ color: "red", message: (err as Error).message });
    }
  };
  return (
    <>
      <Popover position="bottom-end" shadow="md" withArrow>
        <Popover.Target>
          <Tooltip label="Tokens">
            <ActionIcon variant="default" size="lg" aria-label="Pattern tokens" data-testid="pattern-help">
              <IconHelp size={18} />
            </ActionIcon>
          </Tooltip>
        </Popover.Target>
        <Popover.Dropdown maw={420}>
          <Text size="xs" mb={6}>
            {mode === "sort" ? (
              <>
                Each <Code>/</Code> starts a folder; the last part is the file name (the extension is kept). Text around tokens
                stays as typed.
              </>
            ) : (
              <>
                The pattern is matched against the end of each archive&apos;s path: each <Code>/</Code> is a folder, the last part
                the file name (the title loses its extension). Text around tokens must be in the path as typed; <Code>*</Code>{" "}
                skips one folder.
              </>
            )}{" "}
            Click a token to add it.
          </Text>
          <Table fz="xs" withRowBorders={false} verticalSpacing={2}>
            <Table.Tbody>
              {TOKENS.map((t) => (
                <Table.Tr key={t.token}>
                  <Table.Td>
                    {[t.token, ...t.aliases].map((name) => (
                      <Code key={name} mr={4} style={{ cursor: "pointer" }} onClick={() => onChange(`${pattern}{${name}}`)}>
                        {`{${name}}`}
                      </Code>
                    ))}
                  </Table.Td>
                  <Table.Td>{t.field}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          <Text size="xs" c="dimmed" mt={6}>
            {mode === "sort" ? (
              <>
                <Code>{"{title:20}"}</Code> keeps the first 20 characters, so <Code>{"{creator:1}/{creator}/{title}"}</Code> puts Sanix
                under <Code>S/Sanix/</Code>. Case does not matter. A missing value becomes <Code>_unknown</Code>; characters not
                allowed in file names become <Code>_</Code>.
              </>
            ) : (
              <>
                For example <Code>{"{sculptor}/{date} - {title}"}</Code> reads <Code>Sanix/2024-05 - Dragon.zip</Code>. Case does not
                matter; any other name in braces, like <Code>{"{skip}"}</Code>, matches text without keeping it.
              </>
            )}
          </Text>
        </Popover.Dropdown>
      </Popover>
      <Menu position="bottom-end" shadow="md">
        <Menu.Target>
          <Button variant="default" leftSection={<IconBookmark size={16} />} data-testid="saved-patterns">
            Saved
          </Button>
        </Menu.Target>
        <Menu.Dropdown maw={480}>
          <Menu.Item
            leftSection={<IconDeviceFloppy size={14} />}
            disabled={!pattern.trim() || saved.includes(pattern.trim())}
            onClick={() => void store(withPattern(saved, pattern))}
            data-testid="save-pattern"
          >
            Save this pattern
          </Menu.Item>
          {saved.length > 0 && <Menu.Divider />}
          {saved.length === 0 && <Menu.Label>No saved patterns yet</Menu.Label>}
          {saved.map((p) => (
            <Menu.Item
              key={p}
              onClick={() => onChange(p)}
              rightSection={
                <ActionIcon
                  size="xs"
                  variant="subtle"
                  color="gray"
                  aria-label={`Forget ${p}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    void store(withoutPattern(saved, p));
                  }}
                >
                  <IconX size={12} />
                </ActionIcon>
              }
            >
              <Text size="xs" ff="monospace">
                {p}
              </Text>
            </Menu.Item>
          ))}
        </Menu.Dropdown>
      </Menu>
    </>
  );
}
