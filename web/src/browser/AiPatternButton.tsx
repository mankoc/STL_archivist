// "Infer with AI" beside a crawl's path pattern: an `ai_pattern` job reads the
// archive paths below the folder and proposes a pattern, which replaces the field.
import { Button, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, unwrap } from "../api/client";

export function AiPatternButton({ folder, onPattern }: { folder: string; onPattern: (pattern: string) => void }) {
  const [jobId, setJobId] = useState<number | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: async () => unwrap(await api.GET("/api/jobs")), enabled: jobId != null });
  const job = (jobs.data ?? []).find((j) => j.id === jobId);
  const detail = useQuery({
    queryKey: ["job-result", jobId],
    queryFn: async () => unwrap(await api.GET("/api/jobs/{id}", { params: { path: { id: jobId! } } })),
    enabled: job?.state === "done",
  });
  useEffect(() => {
    if (job?.state === "failed" || job?.state === "cancelled") {
      notifications.show({ color: "red", title: "Infer pattern", message: job.message ?? job.state });
      setJobId(null);
    }
  }, [job?.state, job?.message]);
  useEffect(() => {
    const r = detail.data?.result;
    if (r?.type !== "ai_pattern") return;
    onPattern(r.pattern);
    setNote(`Reads ${r.fits} of ${r.sampled} sampled archive paths. Check the sample below.`);
    setJobId(null);
  }, [detail.data]);
  const ask = async () => {
    setNote(null);
    try {
      setJobId(unwrap(await api.POST("/api/jobs", { body: { kind: "ai_pattern", folder } })).id);
    } catch (err) {
      notifications.show({ color: "red", title: "Infer pattern", message: (err as Error).message });
    }
  };
  return (
    <>
      <Button size="xs" variant="light" onClick={ask} loading={jobId != null} style={{ alignSelf: "flex-start" }} data-testid="ai-pattern"
        title="Ask the AI model for a pattern from the archive paths below this folder and the library's known names">
        Infer with AI
      </Button>
      {note && <Text size="xs" c="dimmed">{note}</Text>}
    </>
  );
}
