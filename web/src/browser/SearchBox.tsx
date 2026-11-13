// The library's search box, with its options. The text being typed is its own
// state: a keystroke re-renders the box, not the whole page (the filter pane, the
// table and the detail pane), which sees the text once typing pauses.
import { ActionIcon, Anchor, Checkbox, Popover, SimpleGrid, Stack, Switch, Text, TextInput, Tooltip } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { IconAdjustmentsHorizontal, IconSearch } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { customised, DEFAULT_OPTIONS, placeholder, SEARCH_FIELDS, type SearchOptions } from "./search";

/** How long typing pauses before the search runs. */
export const SEARCH_DELAY = 250;

export function SearchBox({ options, onOptions, onSearch }: {
  options: SearchOptions;
  onOptions: (o: SearchOptions) => void;
  /** The text, once typing pauses. */
  onSearch: (text: string) => void;
}) {
  const [text, setText] = useState("");
  const [settled] = useDebouncedValue(text, SEARCH_DELAY);
  useEffect(() => onSearch(settled), [settled, onSearch]);
  return (
    <TextInput
      size="xs"
      placeholder={placeholder(options)}
      leftSection={<IconSearch size={14} />}
      value={text}
      onChange={(e) => setText(e.currentTarget.value)}
      w={320}
      rightSectionPointerEvents="all"
      rightSection={
        <Popover position="bottom-end" shadow="md" withArrow>
          <Popover.Target>
            <Tooltip label="Search options" openDelay={400}>
              <ActionIcon size="sm" variant={customised(options) ? "light" : "subtle"} color={customised(options) ? undefined : "gray"}
                aria-label="Search options" data-testid="search-options">
                <IconAdjustmentsHorizontal size={14} />
              </ActionIcon>
            </Tooltip>
          </Popover.Target>
          <Popover.Dropdown>
            <Stack gap="xs">
              <Text size="xs" fw={600}>Search in</Text>
              <SimpleGrid cols={2} spacing="xs" verticalSpacing={6}>
                {SEARCH_FIELDS.map((f) => {
                  const on = options.fields.includes(f.value);
                  return (
                    <Checkbox key={f.value} size="xs" label={f.label} checked={on}
                      disabled={on && options.fields.length === 1}
                      onChange={() => onOptions({
                        ...options,
                        fields: SEARCH_FIELDS.map((x) => x.value).filter((v) => (v === f.value ? !on : options.fields.includes(v))),
                      })} />
                  );
                })}
              </SimpleGrid>
              <Switch size="xs" label="Fuzzy: ignore accents, allow typos" checked={options.fuzzy}
                onChange={(e) => onOptions({ ...options, fuzzy: e.currentTarget.checked })} />
              <Text size="xs" c="dimmed" maw={260}>
                {options.fuzzy
                  ? "Every word must appear in one of the fields; words of four letters or more may have a typo (two from seven letters)."
                  : "Finds the text as typed inside any of the fields."}
              </Text>
              <Text size="xs" c="dimmed" maw={260}>
                Name fields and combine: <Text span ff="monospace" size="xs">title="batman" OR creator="loot studios"</Text>,
                with AND, NOT and parentheses.
              </Text>
              {customised(options) && (
                <Anchor size="xs" component="button" onClick={() => onOptions(DEFAULT_OPTIONS)}>Reset</Anchor>
              )}
            </Stack>
          </Popover.Dropdown>
        </Popover>
      }
    />
  );
}
