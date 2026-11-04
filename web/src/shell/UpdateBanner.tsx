// Announces a newer release while the update check is on.
import { Alert, Anchor, Button, Group, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap } from "../api/client";
import { inDesktopShell, installUpdate } from "../desktop";
import { loadDismissed, saveDismissed, UPDATE_REFETCH_MS, versionToShow } from "./updates";

export function UpdateBanner({ enabled }: { enabled: boolean }) {
  const info = useQuery({
    queryKey: ["system-info"],
    queryFn: async () => unwrap(await api.GET("/api/system/info")),
    enabled,
    refetchInterval: UPDATE_REFETCH_MS,
  });
  const [dismissed, setDismissed] = useState(loadDismissed);
  const [installing, setInstalling] = useState(false);
  const update = info.data?.update;
  const version = enabled ? versionToShow(update?.latest, dismissed) : null;
  if (!version) return null;

  const dismiss = () => {
    saveDismissed(version);
    setDismissed(version);
  };
  const install = async () => {
    setInstalling(true);
    try {
      await installUpdate(version);
    } catch (err) {
      notifications.show({ color: "red", message: String(err) });
    } finally {
      setInstalling(false);
    }
  };
  return (
    <Alert color="blue" py={6} radius={0} withCloseButton onClose={dismiss} style={{ flexShrink: 0 }} data-testid="update-banner">
      <Group gap="md">
        <Text size="sm">StlArchivist {version} is available.</Text>
        {update?.release_url && (
          <Anchor size="sm" href={update.release_url} target="_blank" rel="noreferrer">
            What's new
          </Anchor>
        )}
        {inDesktopShell() && update?.installer_url && (
          <Button size="compact-sm" loading={installing} onClick={() => void install()}>
            Download and install
          </Button>
        )}
      </Group>
    </Alert>
  );
}
