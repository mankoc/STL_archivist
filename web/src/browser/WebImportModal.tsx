// Import from a web page: read a model's page (found by the user; the model's
// own link to start with), review what it says against the model, and apply
// what is kept. Sites that answer only a browser are read in the desktop app's
// window (webImport.ts: which ones, the review, the request).
import {
  Alert, Anchor, Box, Button, Checkbox, Chip, Group, Loader, Modal, SimpleGrid, Stack, Table, Text, Textarea, TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconWorldDownload } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, unwrap } from "../api/client";
import { inDesktopShell, readWebPage } from "../desktop";
import { SETTINGS_QUERY } from "../settings/librarySettings";
import { changeCount, importBody, importSummary, needsBrowser, pageUrl, type Review, startReview, WEB_FIELDS, type WebField, type WebPage } from "./webImport";

type Phase = "idle" | "reading" | "window" | "applying";

/** The dialog for model `id`; render it with `key={id}` so each model starts afresh. */
export function WebImportModal({ id, onClose }: { id: number; onClose: () => void }) {
  const queryClient = useQueryClient();
  const model = useQuery({
    queryKey: ["editor", id],
    queryFn: async () => unwrap(await api.GET("/api/models/{id}/editor", { params: { path: { id } } })),
  });
  const settings = useQuery(SETTINGS_QUERY);
  const desktop = inDesktopShell();
  const [url, setUrl] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [blocked, setBlocked] = useState<string | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [pageAddress, setPageAddress] = useState("");

  // Start from the model's own link.
  const link = model.data?.url ?? "";
  useEffect(() => setUrl((u) => u || link), [link]);

  const show = (page: WebPage) => {
    if (!model.data) return;
    setPageAddress(page.url);
    setReview(startReview(model.data, page, settings.data?.max_thumbnails ?? 10));
  };

  const readInWindow = async (address: string) => {
    setPhase("window");
    const captured = await readWebPage(address);
    if (!captured) return;
    setPhase("reading");
    show(unwrap(await api.POST("/api/web/read-captured", { body: captured })));
  };

  const read = async (inWindow: boolean) => {
    const address = pageUrl(url);
    if (!address) {
      setError("Type or paste the address of the model's page (http or https).");
      return;
    }
    setError(null);
    setBlocked(null);
    try {
      if (inWindow) {
        await readInWindow(address);
        return;
      }
      setPhase("reading");
      const r = unwrap(await api.POST("/api/web/read", { body: { url: address } }));
      if (r.page) show(r.page);
      else if (desktop) await readInWindow(address);
      else setBlocked(r.blocked ?? "The site answers only a browser.");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPhase("idle");
    }
  };

  const apply = async () => {
    if (!review) return;
    setPhase("applying");
    setError(null);
    try {
      const r = unwrap(await api.POST("/api/models/{id}/web-import", { params: { path: { id } }, body: importBody(review) }));
      notifications.show({ color: r.failed.length ? "yellow" : undefined, message: importSummary(r), autoClose: r.failed.length ? 10000 : undefined });
      for (const key of ["rows", "tree", "model", "editor", "lookups", "grid"]) await queryClient.invalidateQueries({ queryKey: [key] });
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPhase("idle");
    }
  };

  const busy = phase !== "idle";
  const title = model.data?.title || model.data?.archive_path || "model";
  return (
    <Modal opened onClose={onClose} size={review ? "xl" : "lg"} title={`Import from a web page: ${title}`}>
      {!review ? (
        <Stack>
          <Text size="sm" c="dimmed">
            Paste the address of the model's page (Cults3D, MyMiniFactory, Patreon, a store…). Its title, creator, description, tags
            and images are shown next to the model's before anything changes.
          </Text>
          <TextInput
            label="Model page"
            placeholder="https://…"
            value={url}
            onChange={(e) => setUrl(e.currentTarget.value)}
            onKeyDown={(e) => e.key === "Enter" && !busy && void read(desktop && needsBrowser(url))}
            disabled={busy}
            data-autofocus
            data-testid="web-import-url"
          />
          {phase === "window" && (
            <Alert color="blue" icon={<Loader size={16} />}>
              The page opened in a window. Sign in or pass the check if it asks, go to the model's page, then click <b>Use this page</b> in the
              bar at the bottom of the window.
            </Alert>
          )}
          {blocked && (
            <Alert color="yellow">
              {blocked}. Open this library in the StlArchivist desktop app to read the page in its window.
            </Alert>
          )}
          {error && <Alert color="red">{error}</Alert>}
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>Cancel</Button>
            {desktop && (
              <Button variant="light" onClick={() => void read(true)} disabled={busy}>
                Read in a window
              </Button>
            )}
            <Button leftSection={<IconWorldDownload size={16} />} onClick={() => void read(desktop && needsBrowser(url))} loading={phase === "reading"} disabled={busy && phase !== "reading"}>
              Read
            </Button>
          </Group>
        </Stack>
      ) : (
        <ReviewForm review={review} setReview={setReview} current={model.data} pageAddress={pageAddress}>
          {error && <Alert color="red">{error}</Alert>}
          <Group justify="space-between">
            <Button variant="subtle" onClick={() => setReview(null)} disabled={busy}>
              Another page
            </Button>
            <Group>
              <Button variant="default" onClick={onClose}>Cancel</Button>
              <Button onClick={() => void apply()} loading={phase === "applying"} disabled={changeCount(review) === 0} data-testid="web-import-apply">
                Apply {changeCount(review) || ""}
              </Button>
            </Group>
          </Group>
        </ReviewForm>
      )}
    </Modal>
  );
}

type Current = { title?: string | null; creator?: string | null; source?: string | null; url?: string | null; description?: string | null } | undefined;

/** The page's values next to the model's, each ticked to be applied; tags and images to add. */
function ReviewForm({ review, setReview, current, pageAddress, children }: {
  review: Review; setReview: (r: Review) => void; current: Current; pageAddress: string; children: React.ReactNode;
}) {
  const setField = (key: WebField, patch: Partial<Review["fields"][WebField]>) =>
    setReview({ ...review, fields: { ...review.fields, [key]: { ...review.fields[key], ...patch } } });
  const images = review.images;
  const tickImages = (apply: boolean) => setReview({ ...review, images: images.map((i) => ({ ...i, apply })) });
  return (
    <Stack>
      <Text size="sm">
        From{" "}
        <Anchor href={pageAddress} target="_blank" rel="noopener noreferrer" style={{ wordBreak: "break-all" }}>
          {pageAddress}
        </Anchor>
        . Ticked values replace the model's.
      </Text>
      <Table verticalSpacing={4} fz="sm">
        <Table.Thead>
          <Table.Tr>
            <Table.Th w={110}>Field</Table.Th>
            <Table.Th w="32%">On the model</Table.Th>
            <Table.Th>From the page</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {WEB_FIELDS.map(({ key, label }) => {
            const f = review.fields[key];
            const now = current?.[key] ?? "";
            return (
              <Table.Tr key={key}>
                <Table.Td>
                  <Checkbox label={label} checked={f.apply} disabled={!f.value.trim()} onChange={(e) => setField(key, { apply: e.currentTarget.checked })} />
                </Table.Td>
                <Table.Td>
                  <Text size="sm" c="dimmed" lineClamp={key === "description" ? 4 : 2} style={{ wordBreak: "break-word" }}>
                    {now || "—"}
                  </Text>
                </Table.Td>
                <Table.Td>
                  {key === "description" ? (
                    <Textarea autosize minRows={2} maxRows={8} size="xs" value={f.value} onChange={(e) => setField(key, { value: e.currentTarget.value, apply: true })} />
                  ) : (
                    <TextInput size="xs" value={f.value} onChange={(e) => setField(key, { value: e.currentTarget.value, apply: true })} />
                  )}
                </Table.Td>
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
      <Stack gap={6}>
        <Text size="sm" fw={500}>Add tags</Text>
        {review.tags.length ? (
          <Group gap={6}>
            {review.tags.map((t, i) => (
              <Chip key={t.tag} size="xs" checked={t.apply}
                onChange={(apply) => setReview({ ...review, tags: review.tags.map((x, j) => (j === i ? { ...x, apply } : x)) })}>
                {t.tag}
              </Chip>
            ))}
          </Group>
        ) : (
          <Text size="sm" c="dimmed">No tags the model does not have.</Text>
        )}
      </Stack>
      <Stack gap={6}>
        <Group justify="space-between">
          <Text size="sm" fw={500}>Add images ({images.filter((i) => i.apply).length} of {images.length})</Text>
          <Group gap="xs">
            <Checkbox size="xs" label="Skip images the model already has" checked={review.skipKnown}
              onChange={(e) => setReview({ ...review, skipKnown: e.currentTarget.checked })} />
            <Button size="compact-xs" variant="subtle" onClick={() => tickImages(true)}>All</Button>
            <Button size="compact-xs" variant="subtle" onClick={() => tickImages(false)}>None</Button>
          </Group>
        </Group>
        {images.length ? (
          <SimpleGrid cols={{ base: 3, sm: 5 }} spacing="xs">
            {images.map((img, i) => (
              <PageImage key={img.url} url={img.url} apply={img.apply}
                onToggle={() => setReview({ ...review, images: images.map((x, j) => (j === i ? { ...x, apply: !x.apply } : x)) })} />
            ))}
          </SimpleGrid>
        ) : (
          <Text size="sm" c="dimmed">The page has no images.</Text>
        )}
      </Stack>
      {children}
    </Stack>
  );
}

/** One of the page's images, ticked or not (shown from the site itself). */
function PageImage({ url, apply, onToggle }: { url: string; apply: boolean; onToggle: () => void }) {
  const [broken, setBroken] = useState(false);
  return (
    <Box
      onClick={onToggle}
      style={{
        position: "relative", cursor: "pointer", height: 110, borderRadius: 6, overflow: "hidden",
        outline: apply ? "2px solid var(--mantine-color-blue-filled)" : "1px solid var(--mantine-color-default-border)",
        opacity: apply ? 1 : 0.55,
      }}
      title={url}
    >
      {broken ? (
        <Text size="xs" c="dimmed" p="xs">Not shown here; the server may still download it.</Text>
      ) : (
        <img src={url} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setBroken(true)}
          style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }} />
      )}
      <Checkbox checked={apply} readOnly size="xs" style={{ position: "absolute", top: 6, left: 6, pointerEvents: "none" }} aria-label="Add this image" />
    </Box>
  );
}
