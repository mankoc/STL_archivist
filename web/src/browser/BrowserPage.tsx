// The browser: filter pane, table and detail pane (the desktop's main window).
import { Box, Button, Center, Group, Loader, Text, TextInput } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { IconFilterOff, IconSearch } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap } from "../api/client";
import { DetailPane } from "./DetailPane";
import { FilterPane } from "./FilterPane";
import { EMPTY, type FilterSpec, isEmpty } from "./filters";
import { ModelTable, type Sort, useModelIds } from "./ModelTable";
import classes from "./BrowserPage.module.css";

const SHOW = ["author", "tags", "source", "date", "sculptor", "collection", "subcollection", "category", "release", "scale", "presupports", "path"];

export function BrowserPage() {
  const [filter, setFilter] = useState<FilterSpec>(EMPTY);
  const [searchText, setSearchText] = useState("");
  const [search] = useDebouncedValue(searchText, 250);
  const [sort, setSort] = useState<Sort | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const query = { filter, search, sort };
  const ids = useModelIds(query);

  const tree = useQuery({
    queryKey: ["tree", filter],
    queryFn: async () => unwrap(await api.POST("/api/filter-tree", { body: { filter, show: SHOW, counts: true } })),
    placeholderData: (previous) => previous,
  });

  return (
    <div className={classes.layout}>
      <Box className={classes.filters}>
        <Group justify="space-between" px="xs" py={6} className={classes.toolbar}>
          <Text size="sm" fw={600}>
            Filter
          </Text>
          <Button
            size="compact-xs"
            variant="subtle"
            leftSection={<IconFilterOff size={14} />}
            disabled={isEmpty(filter)}
            onClick={() => setFilter(EMPTY)}
          >
            Clear
          </Button>
        </Group>
        {tree.data ? (
          <FilterPane tree={tree.data} spec={filter} onChange={setFilter} />
        ) : (
          <Center p="md">{tree.isError ? <Text c="red">{tree.error.message}</Text> : <Loader size="sm" />}</Center>
        )}
      </Box>
      <Box className={classes.table}>
        <Group px="xs" py={6} className={classes.toolbar}>
          <TextInput
            size="xs"
            placeholder="Search title, creator, path, tags"
            leftSection={<IconSearch size={14} />}
            value={searchText}
            onChange={(e) => setSearchText(e.currentTarget.value)}
            w={320}
          />
          <Text size="xs" c="dimmed">
            {ids.data ? `${ids.data.length.toLocaleString()} models` : ""}
          </Text>
        </Group>
        <Box className={classes.tableBody}>
          <ModelTable query={query} onSort={setSort} selected={selected} onSelect={setSelected} />
        </Box>
      </Box>
      <Box className={classes.detail}>
        <DetailPane id={selected} />
      </Box>
    </div>
  );
}
