// Find by Image: drop a picture, find models whose thumbnails look like it.
import { Alert, Box, Card, Group, Image, Loader, Modal, SegmentedControl, SimpleGrid, Slider, Stack, Text, Title, UnstyledButton } from "@mantine/core";
import { Dropzone, IMAGE_MIME_TYPE } from "@mantine/dropzone";
import { IconPhotoSearch } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { ApiError, type Schemas } from "../api/client";
import { DetailPane } from "../browser/DetailPane";

type Result = Schemas["SearchResult"];

async function search(file: File, method: string, value: number): Promise<Result> {
  const form = new FormData();
  form.append("file", file, file.name);
  const q = method === "dhash" ? `method=dhash&max_distance=${value}` : `method=clip&threshold=${value}`;
  const resp = await fetch(`/api/search/image?${q}`, { method: "POST", body: form, credentials: "same-origin" });
  const body = await resp.json();
  if (!resp.ok) throw new ApiError(resp.status, body.error ?? resp.statusText);
  return body;
}

export function FindByImagePage() {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [method, setMethod] = useState("dhash");
  const [distance, setDistance] = useState(10);
  const [threshold, setThreshold] = useState(0.85);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<number | null>(null);

  useEffect(() => {
    if (!file) return;
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  useEffect(() => {
    if (!file) return;
    let current = true;
    setBusy(true);
    setError(null);
    const t = setTimeout(async () => {
      try {
        const r = await search(file, method, method === "dhash" ? distance : threshold);
        if (current) setResult(r);
      } catch (err) {
        if (current) {
          setResult(null);
          setError((err as Error).message);
        }
      } finally {
        if (current) setBusy(false);
      }
    }, 250);
    return () => {
      current = false;
      clearTimeout(t);
    };
  }, [file, method, distance, threshold]);

  return (
    <Stack p="md" style={{ overflow: "auto", height: "100%" }}>
      <Group align="flex-start" wrap="nowrap" gap="lg">
        <Card withBorder w={320} style={{ flex: "none" }}>
          <Stack>
            <Dropzone accept={[...IMAGE_MIME_TYPE, "image/tiff"]} multiple={false} onDrop={(files) => files[0] && setFile(files[0])} data-testid="search-drop">
              {preview ? (
                <Image src={preview} radius="sm" alt="" />
              ) : (
                <Stack align="center" gap="xs" py="lg" style={{ pointerEvents: "none" }}>
                  <IconPhotoSearch size={32} />
                  <Text size="sm" c="dimmed" ta="center">
                    Drop an image here, or click to choose one
                  </Text>
                </Stack>
              )}
            </Dropzone>
            <SegmentedControl
              value={method}
              onChange={setMethod}
              data={[
                { value: "dhash", label: "Perceptual hash" },
                { value: "clip", label: "AI embedding" },
              ]}
            />
            {method === "dhash" ? (
              <Box>
                <Text size="sm">Differing bits: {distance}</Text>
                <Text size="xs" c="dimmed">
                  0 finds identical images; larger values find looser matches.
                </Text>
                <Slider min={0} max={40} value={distance} onChange={setDistance} mt="xs" />
              </Box>
            ) : (
              <Box>
                <Text size="sm">Similarity: {threshold.toFixed(2)}</Text>
                <Text size="xs" c="dimmed">
                  Higher values find closer matches.
                </Text>
                <Slider min={0.5} max={1} step={0.01} value={threshold} onChange={setThreshold} mt="xs" />
              </Box>
            )}
          </Stack>
        </Card>
        <Stack flex={1} gap="xs">
          <Group gap="xs">
            <Title order={5}>Results</Title>
            {busy && <Loader size="xs" />}
          </Group>
          {error && <Alert color="red">{error}</Alert>}
          {result && (
            <Text size="sm" c="dimmed" data-testid="search-summary">
              {result.matches.length} model(s)
              {result.capped && " among the closest images; more match, so tighten the threshold"}
              {result.outdated > 0 && `. ${result.outdated} image(s) have hashes in an older format: run Recompute image hashes on the Jobs page`}
            </Text>
          )}
          <SimpleGrid cols={{ base: 2, sm: 3, md: 4, xl: 6 }} spacing="sm">
            {result?.matches.map((m) => (
              <UnstyledButton key={m.model_id} onClick={() => setOpen(m.model_id)} data-testid="search-match">
                <Card withBorder p="xs">
                  {m.thumbnail ? <Image src={m.thumbnail} radius="sm" alt="" h={140} fit="contain" /> : <Box h={140} />}
                  <Text size="sm" truncate mt={4}>
                    {m.title || m.archive}
                  </Text>
                  <Text size="xs" c="dimmed">
                    {result.method === "dhash" ? `${m.score} bits differ` : `similarity ${m.score.toFixed(3)}`}
                  </Text>
                </Card>
              </UnstyledButton>
            ))}
          </SimpleGrid>
        </Stack>
      </Group>
      <Modal opened={open != null} onClose={() => setOpen(null)} size="md" title="Model">
        <Box h="70vh">
          <DetailPane id={open} onEdit={() => {}} />
        </Box>
      </Modal>
    </Stack>
  );
}
