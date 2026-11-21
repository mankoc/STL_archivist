// Settings → Account: this user's appearance and preferences, and in the desktop
// app the programs this computer opens models and archives with.
import { Button, Card, ColorSwatch, Group, SegmentedControl, Switch, Text, Title, Tooltip, useMantineColorScheme } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconCheck } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { type Accent, ACCENT_SETTING, ACCENTS, DEFAULT_ACCENT, isAccent, useSetAccent } from "../accent";
import { api, unwrap } from "../api/client";
import { HIGHLIGHT_WARNINGS } from "../browser/warnings";
import { clearMeshPeek, clearPacker, meshpeekState, packerState, pickMeshPeek, pickPacker, type ProgramState } from "../desktop";
import { CHECK_UPDATES } from "../shell/updates";

function fail(err: unknown) {
  notifications.show({ color: "red", message: (err as Error).message });
}

export function AccountSettings() {
  const { colorScheme, setColorScheme } = useMantineColorScheme();
  const queryClient = useQueryClient();
  const setAccent = useSetAccent();
  const prefs = useQuery({ queryKey: ["me-settings"], queryFn: async () => unwrap(await api.GET("/api/me/settings")) as Record<string, unknown> });
  const stored = prefs.data?.[ACCENT_SETTING];
  const accent: Accent = isAccent(stored) ? stored : DEFAULT_ACCENT;
  const highlight = prefs.data?.[HIGHLIGHT_WARNINGS] === true;
  const chooseHighlight = async (on: boolean) => {
    queryClient.setQueryData(["me-settings"], (d: Record<string, unknown> | undefined) => ({ ...d, [HIGHLIGHT_WARNINGS]: on }));
    try {
      unwrap(await api.PUT("/api/me/settings/{key}", { params: { path: { key: HIGHLIGHT_WARNINGS } }, body: on as never }));
    } catch (err) {
      fail(err);
    }
  };
  const checkUpdates = prefs.data?.[CHECK_UPDATES] === true;
  const chooseCheckUpdates = async (on: boolean) => {
    queryClient.setQueryData(["me-settings"], (d: Record<string, unknown> | undefined) => ({ ...d, [CHECK_UPDATES]: on }));
    try {
      unwrap(await api.PUT("/api/me/settings/{key}", { params: { path: { key: CHECK_UPDATES } }, body: on as never }));
      await queryClient.invalidateQueries({ queryKey: ["system-info"] });
    } catch (err) {
      fail(err);
    }
  };
  const chooseAccent = async (a: Accent) => {
    setAccent(a);
    queryClient.setQueryData(["me-settings"], (d: Record<string, unknown> | undefined) => ({ ...d, [ACCENT_SETTING]: a }));
    try {
      unwrap(await api.PUT("/api/me/settings/{key}", { params: { path: { key: ACCENT_SETTING } }, body: a as never }));
    } catch (err) {
      fail(err);
    }
  };
  return (
    <Card withBorder>
      <Title order={5} mb="sm">
        Appearance
      </Title>
      <SegmentedControl
        value={colorScheme}
        onChange={async (v) => {
          setColorScheme(v as "light" | "dark" | "auto");
          await api.PUT("/api/me/settings/{key}", { params: { path: { key: "color_scheme" } }, body: v as never });
        }}
        data={[
          { value: "auto", label: "System" },
          { value: "light", label: "Light" },
          { value: "dark", label: "Dark" },
        ]}
        data-testid="color-scheme"
      />
      <Text size="sm" fw={500} mt="md" mb={6}>
        Accent colour
      </Text>
      <Group gap={8} data-testid="accent-colours">
        {ACCENTS.map((a) => (
          <Tooltip key={a} label={a[0].toUpperCase() + a.slice(1)}>
            <ColorSwatch
              component="button"
              type="button"
              color={`var(--mantine-color-${a}-filled)`}
              size={26}
              aria-label={`Accent colour ${a}`}
              aria-pressed={a === accent}
              style={{ cursor: "pointer", outline: a === accent ? "2px solid var(--mantine-color-text)" : undefined, outlineOffset: 2 }}
              onClick={() => chooseAccent(a)}
            >
              {a === accent && <IconCheck size={14} color="white" />}
            </ColorSwatch>
          </Tooltip>
        ))}
      </Group>
      <Text size="xs" c="dimmed" mt="xs">
        Buttons, selections and highlights. Saved for your account; change your password from the menu at the top right.
      </Text>
      <Switch
        mt="md"
        label="Highlight models with warnings"
        description="In the library table: unreadable archives in red, other warnings in yellow."
        checked={highlight}
        onChange={(e) => chooseHighlight(e.currentTarget.checked)}
        data-testid="highlight-warnings"
      />
      <Switch
        mt="md"
        label="Check for updates"
        description="The server asks GitHub for a new release when you open StlArchivist, and at most once a day."
        checked={checkUpdates}
        onChange={(e) => chooseCheckUpdates(e.currentTarget.checked)}
        data-testid="check-updates"
      />
    </Card>
  );
}

/** The MeshPeek program "Open in MeshPeek" runs on this computer (the desktop shell keeps it). */
export function MeshPeekSettings() {
  return (
    <ProgramSettings
      title="MeshPeek on this computer" queryKey="meshpeek" state={meshpeekState} pick={pickMeshPeek} clear={clearMeshPeek}
      notFound="Not found (no meshpeek on the PATH or in Program Files)"
      found="Found automatically; choose the program if MeshPeek is somewhere else." testId="meshpeek-settings"
    />
  );
}

/** The archive program "Open in packer" runs on this computer (the desktop shell keeps it). */
export function PackerSettings() {
  return (
    <ProgramSettings
      title="Archive program on this computer" queryKey="packer" state={packerState} pick={pickPacker} clear={clearPacker}
      notFound="Not found (no PeaZip or 7-Zip on the PATH or in Program Files)"
      found="Found automatically; choose another program (PeaZip, 7-Zip, WinRAR, …) to open archives with." testId="packer-settings"
    />
  );
}

function ProgramSettings({ title, queryKey, state, pick, clear, notFound, found, testId }: {
  title: string;
  queryKey: string;
  state: () => Promise<ProgramState | null>;
  pick: () => Promise<unknown>;
  clear: () => Promise<unknown>;
  notFound: string;
  found: string;
  testId: string;
}) {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: [queryKey], queryFn: state });
  const change = async (call: () => Promise<unknown>) => {
    try {
      await call();
      await queryClient.invalidateQueries({ queryKey: [queryKey] });
    } catch (err) {
      fail(err);
    }
  };
  const s = query.data;
  return (
    <Card withBorder data-testid={testId}>
      <Title order={5} mb="sm">
        {title}
      </Title>
      <Text size="sm" style={{ wordBreak: "break-all" }}>
        {s?.program ?? (s?.chosen ? `Not found at ${s.chosen}` : notFound)}
      </Text>
      <Text size="xs" c="dimmed" mb="sm">
        {s?.chosen ? "Chosen here." : found}
      </Text>
      <Group gap="xs">
        <Button size="xs" variant="light" onClick={() => change(pick)}>
          Choose program…
        </Button>
        {s?.chosen && (
          <Button size="xs" variant="subtle" onClick={() => change(clear)}>
            Find automatically
          </Button>
        )}
      </Group>
    </Card>
  );
}
