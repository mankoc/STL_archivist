// Statistics and Column Distribution (the desktop's two statistics dialogs).
import { Card, Group, Progress, SimpleGrid, Select, Stack, Table, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap } from "../api/client";
import { formatSize } from "../format";

const SCALARS: [string, string, "size" | "count"][] = [
  ["total_models", "Models", "count"],
  ["total_images", "Images", "count"],
  ["total_archive_size", "Archive size", "size"],
  ["total_file_size", "STL files (uncompressed)", "size"],
  ["presupported", "Presupported", "count"],
  ["with_warnings", "With warnings", "count"],
  ["no_images", "Without images", "count"],
  ["with_url", "With a URL", "count"],
  ["total_sculptors", "Sculptors", "count"],
  ["total_collections", "Collections", "count"],
  ["total_categories", "Categories", "count"],
  ["total_releases", "Releases", "count"],
  ["total_scales", "Scales", "count"],
  ["total_tags", "Tags", "count"],
];

const COLUMNS = [
  ["creator", "Creator"], ["source", "Type"], ["sculptor", "Sculptor"], ["collection", "Collection"],
  ["subcollection", "Subcollection"], ["category", "Category"], ["release", "Release"], ["release_year", "Year"],
  ["release_month", "Month"], ["scale", "Scale"], ["tag", "Tag"], ["presupports", "Presupports"],
].map(([value, label]) => ({ value, label }));

const RANKING_TITLE = (key: string) => key.replace(/^by_|^top_/, "").replace(/_/g, " ");

export function StatsPage() {
  const stats = useQuery({ queryKey: ["stats"], queryFn: async () => unwrap(await api.GET("/api/stats/library")) });
  const [column, setColumn] = useState("creator");
  const dist = useQuery({
    queryKey: ["stats", "column", column],
    queryFn: async () => unwrap(await api.GET("/api/stats/column", { params: { query: { key: column, top: 50 } } })),
  });
  const s = stats.data;
  return (
    <Stack p="md" style={{ overflow: "auto", height: "100%" }}>
      <Title order={4}>Library</Title>
      <SimpleGrid cols={{ base: 2, sm: 4, lg: 7 }} data-testid="scalars">
        {SCALARS.map(([key, label, kind]) => (
          <Card key={key} withBorder p="sm">
            <Text size="xs" c="dimmed">
              {label}
            </Text>
            <Text fw={600} size="lg">
              {s ? (kind === "size" ? formatSize(s.scalars[key] ?? 0) : (s.scalars[key] ?? 0).toLocaleString()) : "…"}
            </Text>
          </Card>
        ))}
      </SimpleGrid>
      <SimpleGrid cols={{ base: 1, md: 2, lg: 3 }}>
        {Object.entries(s?.rankings ?? {}).map(([key, rows]) => (
          <Card key={key} withBorder>
            <Text fw={600} size="sm" tt="capitalize" mb="xs">
              {RANKING_TITLE(key)}
            </Text>
            <Table fz="sm" verticalSpacing={2}>
              <Table.Tbody>
                {rows.slice(0, 10).map((r, i) => (
                  <Table.Tr key={i}>
                    <Table.Td>{r.value === null ? <Text c="dimmed" span size="sm">(none)</Text> : String(r.value)}</Table.Td>
                    <Table.Td w={70} ta="right">
                      {r.count.toLocaleString()}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Card>
        ))}
      </SimpleGrid>
      <Card withBorder maw={760}>
        <Group justify="space-between" mb="sm">
          <Title order={5}>Column distribution</Title>
          <Select data={COLUMNS} value={column} onChange={(v) => v && setColumn(v)} allowDeselect={false} w={200} data-testid="column-select" />
        </Group>
        <Stack gap={6}>
          {(dist.data?.rows ?? []).map((r) => (
            <div key={r.label}>
              <Group justify="space-between" gap="xs">
                <Text size="sm" truncate flex={1}>
                  {r.label}
                </Text>
                <Text size="xs" c="dimmed">
                  {r.count.toLocaleString()} ({dist.data && dist.data.total ? Math.round((r.count / dist.data.total) * 100) : 0}%)
                </Text>
              </Group>
              <Progress value={dist.data && dist.data.total ? (r.count / dist.data.total) * 100 : 0} size="sm" />
            </div>
          ))}
        </Stack>
      </Card>
    </Stack>
  );
}
