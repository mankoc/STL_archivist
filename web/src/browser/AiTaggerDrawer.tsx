// The AI tagger (the desktop's AI Tagger dialog): review a suggestion per
// model and save it, or process every selected model in one job.
import {
  Alert, Badge, Button, Checkbox, CloseButton, Drawer, Group, Image, Loader, NumberInput, Progress, ScrollArea,
  SegmentedControl, Select, SimpleGrid, Stack, Text, Textarea, TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, unwrap } from "../api/client";
import { addJob, isActive, type Job, type JobRequest, percent, progressText } from "../jobs/jobs";
import { addTag, EMPTY_FIELDS, type Fields, fieldsOf, removeTag, SOURCES, suggestJob } from "./aiTagger";

const MAX_TAGS_KEY = "stl.ai.maxTags";

function storedMaxTags(): number {
  try {
    const v = Number(localStorage.getItem(MAX_TAGS_KEY));
    return Number.isFinite(v) && v >= 0 && localStorage.getItem(MAX_TAGS_KEY) !== null ? v : 20;
  } catch {
    return 20;
  }
}

export function AiTaggerDrawer({ ids, opened, onClose }: { ids: number[]; opened: boolean; onClose: () => void }) {
  const [mode, setMode] = useState("review");
  const [maxTags, setMaxTags] = useState<number>(storedMaxTags);
  const [ignoreMeta, setIgnoreMeta] = useState(false);
  const [overwrite, setOverwrite] = useState(false);
  const status = useQuery({ queryKey: ["ai-status"], queryFn: async () => unwrap(await api.GET("/api/ai/status")), enabled: opened });
  useEffect(() => {
    try {
      localStorage.setItem(MAX_TAGS_KEY, String(maxTags));
    } catch {
      /* per-viewer convenience only */
    }
  }, [maxTags]);

  return (
    <Drawer opened={opened} onClose={onClose} position="right" size="xl" title={`AI tagger · ${ids.length} model(s)`}>
      <Stack>
        {status.data && !status.data.available && (
          <Alert color="orange" title="The AI model is not available">{status.data.detail}</Alert>
        )}
        <Group justify="space-between" align="flex-end">
          <SegmentedControl value={mode} onChange={setMode} data={[{ value: "review", label: "Review one by one" }, { value: "batch", label: "Process all" }]} />
          <Group gap="sm" align="flex-end">
            <NumberInput size="xs" label="Max tags" description="0: no limit" min={0} max={100} w={100} value={maxTags} onChange={(v) => setMaxTags(Number(v) || 0)} />
            <Checkbox size="xs" label="Ignore catalogued metadata" checked={ignoreMeta} onChange={(e) => setIgnoreMeta(e.currentTarget.checked)} />
            <Checkbox size="xs" label="Overwrite filled fields" checked={overwrite} onChange={(e) => setOverwrite(e.currentTarget.checked)} />
          </Group>
        </Group>
        {mode === "review" ? (
          <Review ids={ids} maxTags={maxTags} ignoreMeta={ignoreMeta} overwrite={overwrite} onDone={onClose} />
        ) : (
          <Batch ids={ids} maxTags={maxTags} ignoreMeta={ignoreMeta} overwrite={overwrite} />
        )}
      </Stack>
    </Drawer>
  );
}

function useJobs() {
  return useQuery({ queryKey: ["jobs"], queryFn: async () => unwrap(await api.GET("/api/jobs")) });
}

async function submit(queryClient: ReturnType<typeof useQueryClient>, body: JobRequest) {
  const job = unwrap(await api.POST("/api/jobs", { body }));
  queryClient.setQueryData<Job[]>(["jobs"], (old) => addJob(old, job));
  return job;
}

function JobProgress({ job }: { job: Job }) {
  const pct = percent(job);
  return (
    <Stack gap={4}>
      <Group gap="xs">
        <Loader size="xs" />
        <Text size="xs" c="dimmed">{job.state === "queued" ? "Waiting for other jobs…" : (job.phase ?? "Asking the model… (the first request loads it, which takes a while)")}</Text>
      </Group>
      <Progress value={pct ?? 100} animated striped={pct == null} />
      {progressText(job, Date.now() / 1000) && <Text size="xs" c="dimmed">{progressText(job, Date.now() / 1000)}</Text>}
    </Stack>
  );
}

function Review({ ids, maxTags, ignoreMeta, overwrite, onDone }: { ids: number[]; maxTags: number; ignoreMeta: boolean; overwrite: boolean; onDone: () => void }) {
  const queryClient = useQueryClient();
  const jobs = useJobs();
  const [index, setIndex] = useState(0);
  const id = ids[Math.min(index, ids.length - 1)];
  const job = id != null ? suggestJob(jobs.data, id) : undefined;
  const finished = job != null && job.state === "done";
  const detail = useQuery({
    queryKey: ["job-result", job?.id],
    queryFn: async () => unwrap(await api.GET("/api/jobs/{id}", { params: { path: { id: job!.id } } })),
    enabled: finished,
  });
  const suggestion = detail.data?.result?.type === "ai_suggestion" ? detail.data.result : null;
  const [fields, setFields] = useState<Fields>(EMPTY_FIELDS);
  const [tags, setTags] = useState<string[]>([]);
  const [newTag, setNewTag] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    setFields(fieldsOf(suggestion));
    setTags(suggestion?.tags ?? []);
  }, [suggestion]);
  const model = useQuery({
    queryKey: ["model", id],
    queryFn: async () => unwrap(await api.GET("/api/models/{id}", { params: { path: { id: id! } } })),
    enabled: id != null,
  });
  const m = (model.data?.fields ?? {}) as Record<string, string | number | null>;
  const modelTitle = (m.title as string | null) || "";

  const next = () => (index + 1 < ids.length ? setIndex(index + 1) : onDone());
  const suggest = async () => {
    try {
      await submit(queryClient, { kind: "ai_suggest", id, max_tags: maxTags, ignore_meta: ignoreMeta });
    } catch (err) {
      notifications.show({ color: "red", message: (err as Error).message });
    }
  };
  const save = async () => {
    setSaving(true);
    try {
      unwrap(await api.POST("/api/ai/commit/{id}", { params: { path: { id } }, body: { fields, tags, overwrite } }));
      notifications.show({ message: `Saved ${fields.title || modelTitle || `#${id}`}` });
      next();
    } catch (err) {
      notifications.show({ color: "red", message: (err as Error).message });
    } finally {
      setSaving(false);
    }
  };
  const set = (k: keyof Fields) => (v: string) => setFields((f) => ({ ...f, [k]: v }));
  if (id == null) return <Text c="dimmed">Select models in the table first.</Text>;
  const shownImages = suggestion?.image_ids ?? [];

  return (
    <Stack>
      <Group justify="space-between">
        <div>
          <Text fw={600}>{modelTitle || suggestion?.title || `#${id}`}</Text>
          <Text size="xs" c="dimmed" style={{ wordBreak: "break-all" }}>{(m.archive_path as string | null) ?? ""}</Text>
        </div>
        <Text size="sm" c="dimmed">{index + 1} / {ids.length}</Text>
      </Group>
      {job && isActive(job) ? (
        <JobProgress job={job} />
      ) : (
        <Group gap="xs">
          <Button onClick={suggest} data-testid="ai-suggest">{suggestion ? "Suggest again" : "Suggest"}</Button>
          {job?.state === "failed" && <Text size="sm" c="red">{job.message}</Text>}
        </Group>
      )}
      {shownImages.length > 0 && (
        <SimpleGrid cols={6} spacing={6}>
          {shownImages.map((i) => <Image key={i} src={`/api/images/${i}/thumbnail`} radius="md" h={80} fit="contain" alt="" />)}
        </SimpleGrid>
      )}
      {suggestion && (
        <ScrollArea.Autosize mah="55vh" data-testid="ai-form">
          <Stack gap="xs">
            <Group gap={6} data-testid="ai-tags">
              {tags.map((t) => (
                <Badge key={t} variant="light" tt="none" rightSection={<CloseButton size="xs" onClick={() => setTags(removeTag(tags, t))} aria-label={`Remove ${t}`} />}>
                  {t}
                </Badge>
              ))}
              {tags.length > 0 && <Button size="compact-xs" variant="subtle" onClick={() => setTags([])}>Clear</Button>}
            </Group>
            <TextInput size="xs" placeholder="Add a tag and press Enter" value={newTag} onChange={(e) => setNewTag(e.currentTarget.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { setTags(addTag(tags, newTag)); setNewTag(""); } }} />
            <SimpleGrid cols={2} spacing="xs">
              <TextInput size="xs" label="Title" value={fields.title} onChange={(e) => set("title")(e.currentTarget.value)} />
              <TextInput size="xs" label="Creator" value={fields.creator} onChange={(e) => set("creator")(e.currentTarget.value)} />
              <TextInput size="xs" label="Sculptor" value={fields.sculptor} onChange={(e) => set("sculptor")(e.currentTarget.value)} />
              <TextInput size="xs" label="Collection" value={fields.collection} onChange={(e) => set("collection")(e.currentTarget.value)} />
              <TextInput size="xs" label="Subcollection" value={fields.subcollection} onChange={(e) => set("subcollection")(e.currentTarget.value)} />
              <TextInput size="xs" label="Scale" value={fields.scale} onChange={(e) => set("scale")(e.currentTarget.value)} />
              <Select size="xs" label="Source" data={SOURCES} value={fields.source || null} clearable onChange={(v) => set("source")(v ?? "")} />
              <NumberInput size="xs" label="Year" min={1900} max={2100} value={fields.year ?? ""} onChange={(v) => setFields((f) => ({ ...f, year: v === "" ? null : Number(v) }))} />
            </SimpleGrid>
            <Textarea size="xs" label="Description" autosize minRows={2} value={fields.description} onChange={(e) => set("description")(e.currentTarget.value)} />
          </Stack>
        </ScrollArea.Autosize>
      )}
      <Group justify="space-between">
        <Button variant="default" onClick={next}>Skip</Button>
        <Button onClick={save} loading={saving} disabled={!suggestion} data-testid="ai-save">Save &amp; next</Button>
      </Group>
    </Stack>
  );
}

function Batch({ ids, maxTags, ignoreMeta, overwrite }: { ids: number[]; maxTags: number; ignoreMeta: boolean; overwrite: boolean }) {
  const queryClient = useQueryClient();
  const jobs = useJobs();
  const [jobId, setJobId] = useState<number | null>(null);
  const job = (jobs.data ?? []).find((j) => j.id === jobId);
  const start = async () => {
    try {
      const j = await submit(queryClient, { kind: "ai_tag", ids, overwrite, ignore_meta: ignoreMeta, max_tags: maxTags });
      setJobId(j.id);
    } catch (err) {
      notifications.show({ color: "red", message: (err as Error).message });
    }
  };
  return (
    <Stack>
      <Text size="sm">
        Ask the model about each of the {ids.length} selected model(s) and save the results: {overwrite ? "suggested values replace filled fields" : "only empty fields are filled"}; tags are added to the existing ones.
      </Text>
      {job && isActive(job) ? (
        <JobProgress job={job} />
      ) : (
        <Group>
          <Button onClick={start} data-testid="ai-batch">Process all</Button>
          {job && <Text size="sm" c={job.state === "done" ? "green" : "red"}>{job.message}</Text>}
        </Group>
      )}
      <Text size="xs" c="dimmed">The job keeps running if you close this drawer; follow it on the Jobs page.</Text>
    </Stack>
  );
}
