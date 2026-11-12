// AI metadata check: an `ai_check` job asks the model about each selected model's
// catalogued fields (implausible scales, misspelled names…); the problems are
// listed with the current and suggested values: ticked suggestions are applied
// (one editor save per model), and Edit opens a model in the editor.
import { Button, Checkbox, Drawer, Group, Loader, Progress, Stack, Table, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { api, unwrap } from "../api/client";
import { percent } from "../jobs/jobs";
import { type AiIssue, applyIssues, canApply, formOf } from "./aiFields";

export function AiCheckDrawer({ ids, opened, onClose, onEdit }: { ids: number[]; opened: boolean; onClose: () => void; onEdit: (id: number) => void }) {
  const queryClient = useQueryClient();
  const [jobId, setJobId] = useState<number | null>(null);
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: async () => unwrap(await api.GET("/api/jobs")), enabled: jobId != null });
  const job = (jobs.data ?? []).find((j) => j.id === jobId);
  const finished = job != null && (job.state === "done" || job.state === "failed" || job.state === "cancelled");
  const detail = useQuery({
    queryKey: ["job-result", jobId],
    queryFn: async () => unwrap(await api.GET("/api/jobs/{id}", { params: { path: { id: jobId! } } })),
    enabled: finished,
  });
  const rows = useMemo(() => (detail.data?.result?.type === "ai_issues" ? detail.data.result.rows : []), [detail.data]);
  // Row indexes: ticked (every applicable one at first) and already applied.
  const [ticked, setTicked] = useState<Set<number>>(new Set());
  const [applied, setApplied] = useState<Set<number>>(new Set());
  const [applying, setApplying] = useState(false);
  useEffect(() => {
    setTicked(new Set(rows.flatMap((r, i) => (canApply(r) ? [i] : []))));
    setApplied(new Set());
  }, [rows]);
  useEffect(() => {
    if (!opened) setJobId(null);
  }, [opened]);
  const chosen = [...ticked].filter((i) => !applied.has(i));
  const toggle = (i: number) =>
    setTicked((t) => {
      const n = new Set(t);
      if (n.has(i)) n.delete(i);
      else n.add(i);
      return n;
    });

  const start = async () => {
    try {
      setJobId(unwrap(await api.POST("/api/jobs", { body: { kind: "ai_check", ids } })).id);
    } catch (err) {
      notifications.show({ color: "red", title: "AI check", message: (err as Error).message });
    }
  };

  // One save per model: its editor form with every ticked suggestion for it.
  const apply = async () => {
    setApplying(true);
    const byModel = new Map<number, { index: number; issue: AiIssue }[]>();
    for (const index of chosen) {
      const r = rows[index];
      byModel.set(r.model_id, [...(byModel.get(r.model_id) ?? []), { index, issue: r }]);
    }
    const done: number[] = [];
    const failed: string[] = [];
    for (const [id, list] of byModel) {
      try {
        const model = unwrap(await api.GET("/api/models/{id}/editor", { params: { path: { id } } }));
        const body = applyIssues(formOf(model), list.map((l) => l.issue));
        unwrap(await api.PUT("/api/models/{id}/editor", { params: { path: { id } }, body }));
        done.push(...list.map((l) => l.index));
      } catch (err) {
        failed.push(`${list[0].issue.title}: ${(err as Error).message}`);
      }
    }
    setApplied((a) => new Set([...a, ...done]));
    setApplying(false);
    notifications.show({
      color: failed.length ? "red" : undefined,
      title: "AI check",
      message: `Applied ${done.length} change(s)${failed.length ? `; failed: ${failed.join("; ")}` : ""}.`,
    });
    for (const key of ["rows", "tree", "model", "editor", "lookups"]) void queryClient.invalidateQueries({ queryKey: [key] });
  };

  return (
    <Drawer opened={opened} onClose={onClose} position="right" size="xl" title={<Text fw={600}>AI metadata check</Text>}>
      <Stack gap="sm">
        <Text size="sm">
          Ask the AI model to check the catalogued metadata of the {ids.length} selected model(s) against their paths, file
          names and the library's names: implausible scales, misspelled names, titles that do not match, values in the wrong
          field. Tick the suggestions to take and <b>Apply</b> them, or open a model with <b>Edit</b>.
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
          <>
            <Group>
              <Button onClick={apply} loading={applying} disabled={!chosen.length} data-testid="ai-check-apply">
                Apply selected ({chosen.length})
              </Button>
              <Text size="xs" c="dimmed">Unticked suggestions are left alone; problems without a suggested value can only be fixed by hand.</Text>
            </Group>
            <Table fz="sm" striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th w={32} />
                  <Table.Th>Model</Table.Th>
                  <Table.Th>Field</Table.Th>
                  <Table.Th>Current</Table.Th>
                  <Table.Th>New</Table.Th>
                  <Table.Th>Problem</Table.Th>
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((r, i) => (
                  <Table.Tr key={`${r.model_id}-${r.field}-${i}`} data-testid="ai-issue" style={applied.has(i) ? { opacity: 0.55 } : undefined}>
                    <Table.Td>
                      <Checkbox
                        size="xs"
                        aria-label={`Apply ${r.field} for ${r.title}`}
                        checked={ticked.has(i) || applied.has(i)}
                        disabled={!canApply(r) || applied.has(i)}
                        onChange={() => toggle(i)}
                      />
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm">{r.title}</Text>
                      <Text size="xs" c="dimmed" style={{ wordBreak: "break-all" }}>{r.archive_path}</Text>
                    </Table.Td>
                    <Table.Td><Text size="sm">{r.field}</Text></Table.Td>
                    <Table.Td>
                      <Text size="sm" c="dimmed" td={applied.has(i) ? "line-through" : undefined}>{r.value || "—"}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" fw={500}>{r.suggestion || "—"}{applied.has(i) ? " ✓" : ""}</Text>
                    </Table.Td>
                    <Table.Td><Text size="sm">{r.problem}</Text></Table.Td>
                    <Table.Td><Button size="xs" variant="light" onClick={() => onEdit(r.model_id)}>Edit</Button></Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </>
        )}
      </Stack>
    </Drawer>
  );
}
