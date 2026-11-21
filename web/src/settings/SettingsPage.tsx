// Settings: this user's preferences (and, in the desktop app, this computer's
// MeshPeek), the library's settings (administrators change them), maintenance,
// sidecars (their settings and jobs), backups, compute workers, the
// downloadable components, and user administration. A tab is addressed as
// `#/settings/<tab>`.
import { Stack, Tabs } from "@mantine/core";

import { hasRole, useUser } from "../auth/user";
import { inDesktopShell } from "../desktop";
import { ComponentsPanel } from "../jobs/ComponentsPanel";
import { MaintenanceTab } from "../jobs/MaintenanceTab";
import { BackupsTab, SidecarsTab, WorkersTab } from "../jobs/SettingsTabs";
import { AccountSettings, MeshPeekSettings, PackerSettings } from "./AccountTab";
import { LibrarySettingsForm } from "./LibraryTab";
import { PatternsTab } from "./PatternsTab";
import { settingsTab } from "./tabs";
import { Users } from "./UsersTab";

export function SettingsPage() {
  const user = useUser();
  const admin = hasRole(user, "admin");
  const editor = hasRole(user, "editor");
  const components = admin || inDesktopShell();
  const tabs = [
    "account", "library", "patterns", "maintenance", "sidecars", ...(admin ? ["backups"] : []), "workers",
    ...(components ? ["components"] : []), ...(admin ? ["users"] : []),
  ];
  return (
    <Tabs
      defaultValue={settingsTab(location.hash, tabs)}
      onChange={(t) => history.replaceState(null, "", `#/settings/${t}`)}
      p="md"
      keepMounted={false}
    >
      <Tabs.List mb="md">
        <Tabs.Tab value="account">Account</Tabs.Tab>
        <Tabs.Tab value="library">Library</Tabs.Tab>
        <Tabs.Tab value="patterns">Patterns</Tabs.Tab>
        <Tabs.Tab value="maintenance">Maintenance</Tabs.Tab>
        <Tabs.Tab value="sidecars">Sidecars</Tabs.Tab>
        {admin && <Tabs.Tab value="backups">Backups &amp; transfer</Tabs.Tab>}
        <Tabs.Tab value="workers">Workers</Tabs.Tab>
        {components && <Tabs.Tab value="components">Components</Tabs.Tab>}
        {admin && <Tabs.Tab value="users">Users</Tabs.Tab>}
      </Tabs.List>
      <Tabs.Panel value="account">
        <Stack maw={520}>
          <AccountSettings />
          {inDesktopShell() && <MeshPeekSettings />}
          {inDesktopShell() && <PackerSettings />}
        </Stack>
      </Tabs.Panel>
      <Tabs.Panel value="library">
        <LibrarySettingsForm editable={admin} />
      </Tabs.Panel>
      <Tabs.Panel value="patterns">
        <PatternsTab editor={editor} />
      </Tabs.Panel>
      <Tabs.Panel value="maintenance">
        <MaintenanceTab />
      </Tabs.Panel>
      <Tabs.Panel value="sidecars">
        <SidecarsTab editor={editor} admin={admin} />
      </Tabs.Panel>
      {admin && (
        <Tabs.Panel value="backups">
          <BackupsTab />
        </Tabs.Panel>
      )}
      <Tabs.Panel value="workers">
        <WorkersTab editor={editor} />
      </Tabs.Panel>
      {components && (
        <Tabs.Panel value="components">
          <ComponentsPanel admin={admin} />
        </Tabs.Panel>
      )}
      {admin && (
        <Tabs.Panel value="users">
          <Users />
        </Tabs.Panel>
      )}
    </Tabs>
  );
}
