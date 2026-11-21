// Settings → Library: the library's settings (administrators change them),
// with the AI tagger's provider forms.
import { Autocomplete, Button, Card, Checkbox, Group, NumberInput, PasswordInput, SegmentedControl, SimpleGrid, Stack, Text, Textarea, TextInput, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, type Schemas, unwrap } from "../api/client";
import { type LibrarySettings, SETTINGS_QUERY } from "./librarySettings";
import { fetchFreeVisionModels, modelSlug } from "./openrouter";

function fail(err: unknown) {
  notifications.show({ color: "red", message: (err as Error).message });
}

export function LibrarySettingsForm({ editable }: { editable: boolean }) {
  const queryClient = useQueryClient();
  const settings = useQuery(SETTINGS_QUERY);
  const [form, setForm] = useState<LibrarySettings | null>(null);
  const [patterns, setPatterns] = useState("");
  useEffect(() => {
    if (settings.data) {
      setForm(settings.data);
      setPatterns(settings.data.presupport_patterns.join("\n"));
    }
  }, [settings.data]);
  if (!form) return null;
  const set = <K extends keyof LibrarySettings>(k: K, v: LibrarySettings[K]) => setForm({ ...form, [k]: v });
  const check = (k: keyof LibrarySettings, label: string) => (
    <Checkbox label={label} checked={Boolean(form[k])} disabled={!editable} onChange={(e) => set(k, e.currentTarget.checked as never)} />
  );
  const save = async () => {
    try {
      const body = { ...form, presupport_patterns: patterns.split("\n").map((p) => p.trim()).filter(Boolean) };
      const saved = unwrap(await api.PUT("/api/settings/library", { body }));
      queryClient.setQueryData(["settings"], saved);
      notifications.show({ message: "Library settings saved." });
    } catch (err) {
      fail(err);
    }
  };
  return (
    <Stack maw={760}>
      {!editable && (
        <Text size="sm" c="dimmed">
          Only administrators can change these settings.
        </Text>
      )}
      <Card withBorder>
        <Title order={5} mb="sm">
          Crawler
        </Title>
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <NumberInput label="Thumbnails per archive" min={0} max={1000} value={form.max_thumbnails} disabled={!editable} onChange={(v) => set("max_thumbnails", Number(v) || 0)} />
          <NumberInput label="Thumbnail size (px)" min={1} max={10000} value={form.max_thumb_size} disabled={!editable} onChange={(v) => set("max_thumb_size", Number(v) || 1)} />
          <NumberInput
            label="Model viewer captures (px)"
            min={1}
            max={10000}
            value={form.view_thumb_size}
            disabled={!editable}
            onChange={(v) => set("view_thumb_size", Number(v) || 1)}
          />
          <NumberInput label="Parallel workers" min={1} max={64} value={form.max_workers} disabled={!editable} onChange={(v) => set("max_workers", Number(v) || 1)} />
          <NumberInput
            label="Check monitored folders every (minutes)"
            min={1}
            max={1440}
            value={form.monitor_interval_minutes}
            disabled={!editable}
            onChange={(v) => set("monitor_interval_minutes", Number(v) || 1)}
          />
        </SimpleGrid>
        <Stack gap="xs" mt="sm">
          {check("recurse_subdirs", "Crawl subfolders")}
          {check("detect_changes_by_size", "Detect changed archives by size only (ignore modification times)")}
          {check("ai_embeddings_enabled", "AI image embeddings")}
          {check("ai_embeddings_during_crawl", "Compute embeddings during crawls")}
        </Stack>
      </Card>
      <Card withBorder>
        <Title order={5} mb="sm">
          Warnings
        </Title>
        <Stack gap="xs">
          {check("warn_presupported_stl", "Models with presupported STL files")}
          {check("warn_no_images", "Archives without images")}
          {check("warn_nested_archive", "Nested archives")}
          {check("warn_no_printable", "Archives without printable files")}
          <Group align="flex-end">
            {check("warn_low_compression", "Low compression rate, above")}
            <NumberInput
              size="xs"
              w={100}
              min={0}
              max={1}
              step={0.01}
              decimalScale={2}
              value={form.low_compression_threshold}
              disabled={!editable || !form.warn_low_compression}
              onChange={(v) => set("low_compression_threshold", Number(v))}
            />
          </Group>
          <Textarea
            label="Presupported file name patterns"
            description="One regular expression per line, matched case-insensitively against STL file names"
            autosize
            minRows={3}
            styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)" } }}
            value={patterns}
            disabled={!editable}
            onChange={(e) => setPatterns(e.currentTarget.value)}
          />
        </Stack>
      </Card>
      <Card withBorder>
        <Title order={5} mb="sm">
          Archive files
        </Title>
        <Stack gap="xs">
          {check("file_sorting_enabled", "Allow Sort files (moves archives)")}
          {check("allow_file_delete", "Allow Delete files")}
          {check("use_trash_folder", "Move deleted archives to the trash folder instead of deleting them")}
          <TextInput
            label="Trash folder"
            description="A folder on the server; without one, deleted archives are gone for good"
            value={form.trash_folder_path}
            disabled={!editable}
            onChange={(e) => set("trash_folder_path", e.currentTarget.value)}
          />
        </Stack>
      </Card>
      <Card withBorder>
        <Title order={5} mb="sm">
          AI tagger
        </Title>
        <Stack gap="xs">
          <SegmentedControl
            w="fit-content"
            value={form.ai_provider}
            disabled={!editable}
            onChange={(v) => set("ai_provider", v)}
            data={[
              { value: "local", label: "Local model (Settings → Components)" },
              { value: "openrouter", label: "OpenRouter" },
              { value: "openai", label: "OpenAI-compatible endpoint" },
            ]}
          />
          {form.ai_provider === "openrouter" && (
            <OpenRouterSettings model={form.openrouter_model} editable={editable} onModel={(m) => set("openrouter_model", m)} />
          )}
          {form.ai_provider === "openai" && (
            <OpenAiSettings model={form.openai_model} editable={editable} onModel={(m) => set("openai_model", m.trim())} />
          )}
          <NumberInput
            label="Temperature"
            description="Lower keeps suggestions literal and grounded in the images; higher is more creative"
            min={0}
            max={2}
            step={0.05}
            decimalScale={2}
            w={220}
            value={form.ai_temperature}
            disabled={!editable}
            onChange={(v) => set("ai_temperature", Number(v) || 0)}
          />
        </Stack>
      </Card>
      {editable && (
        <Group>
          <Button onClick={save} data-testid="save-settings">
            Save
          </Button>
        </Group>
      )}
    </Stack>
  );
}

// The OpenRouter model (part of the library settings form) and its API key
// (saved at once, never shown again).
function OpenRouterSettings({ model, editable, onModel }: { model: string; editable: boolean; onModel: (m: string) => void }) {
  const queryClient = useQueryClient();
  const models = useQuery({ queryKey: ["openrouter-models"], queryFn: fetchFreeVisionModels, staleTime: 3_600_000, retry: false });
  const keyState = useQuery({ queryKey: ["openrouter-key"], queryFn: async () => unwrap(await api.GET("/api/ai/openrouter")) });
  const [key, setKey] = useState("");
  const saveKey = async (value: string) => {
    try {
      queryClient.setQueryData(["openrouter-key"], unwrap(await api.PUT("/api/ai/openrouter", { body: { key: value } })));
      queryClient.invalidateQueries({ queryKey: ["ai-status"] });
      setKey("");
      notifications.show({ message: value ? "OpenRouter API key saved." : "OpenRouter API key removed." });
    } catch (err) {
      fail(err);
    }
  };
  return (
    <>
      <Autocomplete
        label="Model"
        description={
          (models.isError ? "OpenRouter's model list could not be read. " : "Suggestions: OpenRouter's free models that read images. ") +
          "Or paste any model slug from openrouter.ai (it must read images; paid models are charged to the key)."
        }
        placeholder="vendor/model:free"
        w={420}
        data={models.data ?? []}
        value={model}
        disabled={!editable}
        onChange={(v) => onModel(modelSlug(v))}
      />
      <Group align="flex-end">
        <PasswordInput
          label="API key"
          description={keyState.data?.key_set ? "A key is set; enter a new one to replace it" : "No key is set (openrouter.ai, Keys)"}
          w={420}
          value={key}
          disabled={!editable}
          onChange={(e) => setKey(e.currentTarget.value)}
          data-testid="openrouter-key"
        />
        {editable && (
          <Button variant="default" disabled={!key.trim()} onClick={() => saveKey(key.trim())}>
            Save key
          </Button>
        )}
        {editable && keyState.data?.key_set && (
          <Button variant="subtle" color="red" onClick={() => saveKey("")}>
            Remove key
          </Button>
        )}
      </Group>
    </>
  );
}

// The model asked at the OpenAI-compatible endpoint (part of the library
// settings form), and the endpoint's address and key (saved at once, in
// server.db; the key is never shown again).
function OpenAiSettings({ model, editable, onModel }: { model: string; editable: boolean; onModel: (m: string) => void }) {
  const queryClient = useQueryClient();
  const endpoint = useQuery({ queryKey: ["openai-endpoint"], queryFn: async () => unwrap(await api.GET("/api/ai/openai")) });
  const [url, setUrl] = useState<string | null>(null);
  const [key, setKey] = useState("");
  const shownUrl = url ?? endpoint.data?.url ?? "";
  const saveEndpoint = async (body: Schemas["OpenAiEndpoint"], message: string) => {
    try {
      queryClient.setQueryData(["openai-endpoint"], unwrap(await api.PUT("/api/ai/openai", { body })));
      queryClient.invalidateQueries({ queryKey: ["ai-status"] });
      setUrl(null);
      setKey("");
      notifications.show({ message });
    } catch (err) {
      fail(err);
    }
  };
  return (
    <>
      <Autocomplete
        label="Model"
        description="The model's name at the endpoint; it must read images"
        placeholder="gemini-flash-lite-latest"
        w={420}
        data={OPENAI_MODELS}
        value={model}
        disabled={!editable}
        onChange={onModel}
      />
      <Autocomplete
        label="Endpoint"
        description="Base address of an OpenAI-compatible API (requests go to …/chat/completions). Empty: Google's, for Gemini"
        w={420}
        data={OPENAI_ENDPOINTS}
        value={shownUrl}
        disabled={!editable}
        onChange={setUrl}
        data-testid="openai-url"
      />
      <Group align="flex-end">
        <PasswordInput
          label="API key"
          description={endpoint.data?.key_set ? "A key is set; enter a new one to replace it" : "No key is set (for Gemini: aistudio.google.com, Get API key)"}
          w={420}
          value={key}
          disabled={!editable}
          onChange={(e) => setKey(e.currentTarget.value)}
          data-testid="openai-key"
        />
        {editable && (
          <Button
            variant="default"
            disabled={!key.trim() && url === null}
            onClick={() => saveEndpoint(key.trim() ? { url: shownUrl, key: key.trim() } : { url: shownUrl }, "Endpoint saved.")}
          >
            Save endpoint
          </Button>
        )}
        {editable && endpoint.data?.key_set && (
          <Button variant="subtle" color="red" onClick={() => saveEndpoint({ url: shownUrl, key: "" }, "API key removed.")}>
            Remove key
          </Button>
        )}
      </Group>
    </>
  );
}

const OPENAI_MODELS = ["gemini-flash-lite-latest", "gemini-flash-latest", "gpt-4.1-mini", "gpt-4o-mini"];
const OPENAI_ENDPOINTS = ["https://generativelanguage.googleapis.com/v1beta/openai", "https://api.openai.com/v1"];
