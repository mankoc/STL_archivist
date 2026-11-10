// AI metadata check: an `ai_check` job asks the model about each selected model's
// catalogued fields (implausible scales, misspelled names…); the problems are
// listed with the model's suggestion, and Edit opens the model in the editor.
import { Button, Drawer, Group, Loader, Progress, Stack, Table, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, unwrap } from "../api/client";
import { percent } from "../jobs/jobs";

export function AiCheckDrawer({ ids, opened, onClose, onEdit }: { ids: number[]; opened: boolean; onClose: () => void; onEdit: (id: number) => void }) {
  const [jobId, setJobId] = useState<number | null>(null);
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: async () => unwrap(await api.GET("/api/jobs")), enabled: jobId != null });
  const job = (jobs.data ?? []).find((j) => j.id === jobId);
  const finished = job != null && (job.state === "done" || job.state === "failed" || job.state === "cancelled");
  const detail = useQuery({
    queryKey: ["job-result", jobId],
    queryFn: async () => unwrap(await api.GET("/api/jobs/{id}", { params: { path: { id: jobId! } } })),
    enabled: finished,
  });
  const rows = detail.data?.result?.type === "ai_issues" ? detail.data.result.rows : [];
  useEffect(() => {
    if (!opened) setJobId(null);
  }, [opened]);

  const start = async () => {
    try {
      setJobId(unwrap(await api.POST("/api/jobs", { body: { kind: "ai_check", ids } })).id);
    } catch (err) {
      notifications.show({ color: "red", title: "AI check", message: (err as Error).message });
    }
  };

  return (
    <Drawer opened={opened} onClose={onClose} position="right" size="xl" title={<Text fw={600}>AI metadata check</Text>}>
      <Stack gap="sm">
        <Text size="sm">
          Ask the AI model to check the catalogued metadata of the {ids.length} selected model(s) against their paths, file
          names and the library's names: implausible scales, misspelled names, titles that do not match, values in the wrong
          field. Nothing is changed; open a model with <b>Edit</b> to fix it.
        </Text>
        <Group>
          <Button onClick={start} loading={job != null && !finished} disabled={!ids.length} data-testid="ai-check-start">
            {finished ? "Check again" : `Check ${ids.length} model(s)`}
          </Button>
          {job?.message && finished && <Text size="xs" c={job.state === "done" ? "dimmed" : "red"}>{job.message}</Text>}
        </Group>
        {job && !finished && (
          <Stack gap={4}>
            <Group gap="xs">
              <Loader size="xs" />
              <Text size="xs" c="dimmed">{job.state === "queued" ? "Waiting for other jobs…" : (job.phase ?? "Asking the model…")}</Text>
            </Group>
            <Progress value={percent(job) ?? 100} animated />
          </Stack>
        )}
        {finished && rows.length === 0 && job.state === "done" && <Text size="sm" c="dimmed">No problems found.</Text>}
        {rows.length > 0 && (
          <Table fz="sm" striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Model</Table.Th>
                <Table.Th>Field</Table.Th>
                <Table.Th>Problem</Table.Th>
                <Table.Th>Suggestion</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((r, i) => (
                <Table.Tr key={`${r.model_id}-${r.field}-${i}`} data-testid="ai-issue">
                  <Table.Td>
                    <Text size="sm">{r.title}</Text>
                    <Text size="xs" c="dimmed" style={{ wordBreak: "break-all" }}>{r.archive_path}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">{r.field}</Text>
                    {r.value && <Text size="xs" c="dimmed">{r.value}</Text>}
                  </Table.Td>
                  <Table.Td><Text size="sm">{r.problem}</Text></Table.Td>
                  <Table.Td><Text size="sm" fw={500}>{r.suggestion}</Text></Table.Td>
                  <Table.Td><Button size="xs" variant="light" onClick={() => onEdit(r.model_id)}>Edit</Button></Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Stack>
    </Drawer>
  );
}
