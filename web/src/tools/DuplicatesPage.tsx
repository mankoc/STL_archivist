// Find Duplicates: STL file lists, images (perceptual hash or AI embedding)
// and metadata. Each search is a server job; its result stays with the job,
// so the newest one is shown again when the page is reopened.
import {
  Alert, Anchor, Badge, Box, Button, Card, Checkbox, Group, Modal, NumberInput, Pagination, Progress, SegmentedControl,
  SimpleGrid, Stack, Table, Tabs, Text, TextInput,
} from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useMemo, useState } from "react";

import { api, unwrap } from "../api/client";
import { hasRole, useUser } from "../auth/user";
import { DetailPane } from "../browser/DetailPane";
import { formatSize } from "../format";
import { addJob, type Job, type JobRequest, isActive, percent, progressText } from "../jobs/jobs";
import { type Finder, latestJob, matches, METADATA_COLUMNS, pageCount, pageOf, percentLabel, scoreLabel } from "./duplicates";

type Compare = number[];

export function DuplicatesPage() {
  const [tab, setTab] = useState<Finder>("content");
  const [compare, setCompare] = useState<Compare | null>(null);
  return (
    <Stack p="md" style={{ overflow: "auto", height: "100%" }}>
      <Tabs value={tab} onChange={(v) => v && setTab(v as Finder)} keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="content">STL files</Tabs.Tab>
          <Tabs.Tab value="images">Images</Tabs.Tab>
          <Tabs.Tab value="metadata">Metadata</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="content" pt="md">
          <ContentFinder onCompare={setCompare} />
        </Tabs.Panel>
        <Tabs.Panel value="images" pt="md">
          <ImageFinder onCompare={setCompare} />
        </Tabs.Panel>
        <Tabs.Panel value="metadata" pt="md">
          <MetadataFinder onCompare={setCompare} />
        </Tabs.Panel>
      </Tabs>
      <Modal opened={compare != null} onClose={() => setCompare(null)} size={compare && compare.length > 1 ? "90%" : "md"} title="Compare">
        <SimpleGrid cols={Math.min(compare?.length ?? 1, 3)}>
          {compare?.map((id) => (
            <Box key={id} h="75vh" data-testid="compare-pane">
              <DetailPane id={id} onEdit={() => {}} />
            </Box>
          ))}
        </SimpleGrid>
      </Modal>
    </Stack>
  );
}

/** The newest job of a finder, its progress, and its result once done. */
function useFinder(finder: Finder) {
  const queryClient = useQueryClient();
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: async () => unwrap(await api.GET("/api/jobs")) });
  const job = latestJob(jobs.data, finder);
  const done = job?.state === "done" || job?.state === "cancelled";
  const detail = useQuery({
    queryKey: ["job-result", job?.id],
    queryFn: async () => unwrap(await api.GET("/api/jobs/{id}", { params: { path: { id: job!.id } } })),
    enabled: job != null && done,
  });
  const [error, setError] = useState<string | null>(null);
  const start = async (request: JobRequest) => {
    setError(null);
    try {
      const created = unwrap(await api.POST("/api/jobs", { body: request }));
      queryClient.setQueryData<Job[]>(["jobs"], (old) => addJob(old, created));
    } catch (err) {
      setError((err as Error).message);
    }
  };
  const cancel = async () => {
    if (job) await api.DELETE("/api/jobs/{id}", { params: { path: { id: job.id } } });
  };
  return { job, result: done ? detail.data?.result ?? null : null, error, start, cancel };
}

function FinderFrame({
  finder, controls, request, children,
}: { finder: ReturnType<typeof useFinder>; controls: ReactNode; request: () => JobRequest; children: ReactNode }) {
  const user = useUser();
  const { job, error } = finder;
  const running = job != null && isActive(job);
  const pct = job ? percent(job) : null;
  return (
    <Stack>
      <Card withBorder>
        <Group align="flex-end" gap="lg">
          {controls}
          {running ? (
            <Button color="red" variant="light" onClick={finder.cancel}>
              Stop
            </Button>
          ) : (
            <Button onClick={() => finder.start(request())} disabled={!hasRole(user, "editor")} data-testid="find">
              Find
            </Button>
          )}
        </Group>
        {!hasRole(user, "editor") && (
          <Text size="xs" c="dimmed" mt="xs">
            Searching runs as a server job, which needs the editor role.
          </Text>
        )}
        {running && (
          <Stack gap={4} mt="sm">
            <Text size="xs" c="dimmed">
              {job.state === "queued" ? "Waiting for other jobs…" : (job.phase ?? "Searching…")}
            </Text>
            <Progress value={pct ?? 100} animated striped={pct == null} />
            {progressText(job, Date.now() / 1000) && <Text size="xs" c="dimmed">{progressText(job, Date.now() / 1000)}</Text>}
          </Stack>
        )}
        {job?.state === "failed" && <Alert color="red" mt="sm">{job.message}</Alert>}
        {error && <Alert color="red" mt="sm">{error}</Alert>}
      </Card>
      {children}
    </Stack>
  );
}

function SearchAndPages({ text, setText, page, setPage, total, summary }: {
  text: string; setText: (t: string) => void; page: number; setPage: (p: number) => void; total: number; summary: ReactNode;
}) {
  return (
    <Group justify="space-between">
      <Group gap="sm">
        <TextInput placeholder="Filter by title or path" value={text} onChange={(e) => { setText(e.currentTarget.value); setPage(1); }} w={260} size="xs" />
        <Text size="sm" c="dimmed" data-testid="dupes-summary">
          {summary}
        </Text>
      </Group>
      {pageCount(total) > 1 && <Pagination size="sm" total={pageCount(total)} value={page} onChange={setPage} />}
    </Group>
  );
}

function Archive({ title, path, onOpen }: { title: string | null | undefined; path: string | null | undefined; onOpen: () => void }) {
  return (
    <Box>
      <Anchor size="sm" onClick={onOpen} component="button">
        {title || "(untitled)"}
      </Anchor>
      <Text size="xs" c="dimmed" style={{ wordBreak: "break-all" }}>
        {path}
      </Text>
    </Box>
  );
}

function ContentFinder({ onCompare }: { onCompare: (c: Compare) => void }) {
  const finder = useFinder("content");
  const [threshold, setThreshold] = useState<number>(80);
  const [text, setText] = useState("");
  const [page, setPage] = useState(1);
  const result = finder.result?.type === "content" ? finder.result : null;
  const rows = useMemo(() => (result?.pairs ?? []).filter((p) => matches(text, p.title_a, p.path_a, p.title_b, p.path_b)), [result, text]);
  return (
    <FinderFrame
      finder={finder}
      request={() => ({ kind: "find_content_duplicates", threshold: threshold / 100 })}
      controls={
        <NumberInput label="Shared STL files (at least)" suffix=" %" min={1} max={100} value={threshold} onChange={(v) => setThreshold(Number(v) || 80)} w={200} />
      }
    >
      {result && (
        <>
          <SearchAndPages text={text} setText={setText} page={page} setPage={setPage} total={rows.length}
            summary={`${result.pairs.length} archive pair(s) share STL files (same name and size)`} />
          <Table striped highlightOnHover fz="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Archive A</Table.Th>
                <Table.Th>Archive B</Table.Th>
                <Table.Th w={110}>Shared</Table.Th>
                <Table.Th w={90} />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {pageOf(rows, page).map((p) => (
                <Table.Tr key={`${p.id_a}-${p.id_b}`} data-testid="dupe-row">
                  <Table.Td><Archive title={p.title_a} path={p.path_a} onOpen={() => onCompare([p.id_a])} /></Table.Td>
                  <Table.Td><Archive title={p.title_b} path={p.path_b} onOpen={() => onCompare([p.id_b])} /></Table.Td>
                  <Table.Td>
                    <Text size="sm">{percentLabel(p.similarity)}</Text>
                    <Text size="xs" c="dimmed">{p.overlap} of {p.total_a} / {p.total_b}</Text>
                  </Table.Td>
                  <Table.Td><Button size="compact-xs" variant="light" onClick={() => onCompare([p.id_a, p.id_b])}>Compare</Button></Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </>
      )}
    </FinderFrame>
  );
}

function ImageFinder({ onCompare }: { onCompare: (c: Compare) => void }) {
  const finder = useFinder("images");
  const [method, setMethod] = useState("dhash");
  const [distance, setDistance] = useState<number>(10);
  const [similarity, setSimilarity] = useState<number>(0.9);
  const [maxModels, setMaxModels] = useState<number>(10);
  const [text, setText] = useState("");
  const [page, setPage] = useState(1);
  const result = finder.result?.type === "images" ? finder.result : null;
  const rows = useMemo(() => (result?.pairs ?? []).filter((p) => matches(text, p.title_a, p.path_a, p.title_b, p.path_b)), [result, text]);
  return (
    <FinderFrame
      finder={finder}
      request={() => ({ kind: "find_image_duplicates", method, max_distance: distance, threshold: similarity, max_models: maxModels })}
      controls={
        <>
          <SegmentedControl value={method} onChange={setMethod} data={[{ value: "dhash", label: "Perceptual hash" }, { value: "clip", label: "AI embedding" }]} />
          {method === "dhash" ? (
            <NumberInput label="Differing bits (at most)" min={0} max={64} value={distance} onChange={(v) => setDistance(Number(v) || 0)} w={170} />
          ) : (
            <NumberInput label="Similarity (at least)" min={0.5} max={1} step={0.01} decimalScale={2} value={similarity} onChange={(v) => setSimilarity(Number(v) || 0.9)} w={170} />
          )}
          <NumberInput label="Skip images in more than" description="models (banners, logos)" min={1} value={maxModels} onChange={(v) => setMaxModels(Number(v) || 10)} w={190} />
        </>
      }
    >
      {result && (
        <>
          <SearchAndPages text={text} setText={setText} page={page} setPage={setPage} total={rows.length}
            summary={
              `${result.pairs.length} archive pair(s) from ${result.images.toLocaleString()} images` +
              (result.shared_skipped ? `; ${result.shared_skipped} shared image(s) skipped` : "") +
              (result.truncated ? "; only the best pairs are kept, so tighten the threshold" : "")
            } />
          <Table striped highlightOnHover fz="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Archive A</Table.Th>
                <Table.Th>Archive B</Table.Th>
                <Table.Th w={150}>Best match</Table.Th>
                <Table.Th w={90} />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {pageOf(rows, page).map((p) => (
                <Table.Tr key={`${p.id_a}-${p.id_b}`} data-testid="dupe-row">
                  <Table.Td>
                    <Archive title={p.title_a} path={p.path_a} onOpen={() => onCompare([p.id_a])} />
                    <Text size="xs" c="dimmed">{formatSize(p.size_a)} · {p.imgs_a} image(s)</Text>
                  </Table.Td>
                  <Table.Td>
                    <Archive title={p.title_b} path={p.path_b} onOpen={() => onCompare([p.id_b])} />
                    <Text size="xs" c="dimmed">{formatSize(p.size_b)} · {p.imgs_b} image(s)</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">{scoreLabel(result.method, p.best)}</Text>
                    <Text size="xs" c="dimmed">{p.pairs} matching image pair(s)</Text>
                  </Table.Td>
                  <Table.Td><Button size="compact-xs" variant="light" onClick={() => onCompare([p.id_a, p.id_b])}>Compare</Button></Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </>
      )}
    </FinderFrame>
  );
}

function MetadataFinder({ onCompare }: { onCompare: (c: Compare) => void }) {
  const finder = useFinder("metadata");
  const [columns, setColumns] = useState<string[]>(["title"]);
  const [method, setMethod] = useState("exact");
  const [distance, setDistance] = useState<number>(3);
  const [text, setText] = useState("");
  const [page, setPage] = useState(1);
  const result = finder.result?.type === "metadata" ? finder.result : null;
  const groups = useMemo(
    () => (result?.groups ?? []).filter((g) => matches(text, ...g.models.flatMap((m) => [m.title, m.value]))),
    [result, text],
  );
  return (
    <FinderFrame
      finder={finder}
      request={() => ({ kind: "find_metadata_duplicates", columns, method, max_distance: distance })}
      controls={
        <>
          <Checkbox.Group label="Columns" value={columns} onChange={setColumns}>
            <Group gap="sm" mt={6} maw={520}>
              {METADATA_COLUMNS.map((c) => <Checkbox key={c.value} value={c.value} label={c.label} size="xs" />)}
            </Group>
          </Checkbox.Group>
          <SegmentedControl value={method} onChange={setMethod}
            data={[{ value: "exact", label: "Exact" }, { value: "levenshtein", label: "Levenshtein" }, { value: "soundex", label: "Soundex" }]} />
          {method === "levenshtein" && (
            <NumberInput label="Edits (at most)" min={1} max={20} value={distance} onChange={(v) => setDistance(Number(v) || 3)} w={130} />
          )}
        </>
      }
    >
      {result && (
        <>
          <SearchAndPages text={text} setText={setText} page={page} setPage={setPage} total={groups.length}
            summary={`${result.groups.length} group(s)`} />
          <Stack gap="xs">
            {pageOf(groups, page).map((g, i) => (
              <Card key={i} withBorder p="xs" data-testid="dupe-group">
                <Group justify="space-between" mb={4}>
                  <Group gap="xs">
                    <Badge variant="light">{g.column}</Badge>
                    <Text size="xs" c="dimmed">{g.similarity}</Text>
                  </Group>
                  <Button size="compact-xs" variant="light" onClick={() => onCompare(g.models.slice(0, 3).map((m) => m.id))}>
                    Compare
                  </Button>
                </Group>
                <Table fz="sm" verticalSpacing={2}>
                  <Table.Tbody>
                    {g.models.map((m) => (
                      <Table.Tr key={m.id}>
                        <Table.Td w="45%">{m.value}</Table.Td>
                        <Table.Td>
                          <Anchor size="sm" component="button" onClick={() => onCompare([m.id])}>{m.title || `#${m.id}`}</Anchor>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Card>
            ))}
          </Stack>
        </>
      )}
    </FinderFrame>
  );
}
