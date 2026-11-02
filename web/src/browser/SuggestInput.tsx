// A text box suggesting the database's values as it is typed in (suggest.ts):
// ↑/↓ choose a suggestion, Enter or a click takes it, Escape closes the list.
import { Combobox, TextInput, type TextInputProps, useCombobox } from "@mantine/core";
import { type KeyboardEvent, useMemo } from "react";

import { applySuggestion, suggestions } from "./suggest";

type Props = Omit<TextInputProps, "value" | "onChange"> & {
  value: string;
  onChange: (value: string) => void;
  options: readonly string[];
  /** Comma-separated: suggest for the last item. */
  commaList?: boolean;
  /** A suggestion was taken (after `onChange`). */
  onPick?: (value: string) => void;
  /** Enter pressed while no suggestion is chosen, or Escape. */
  onKey?: (key: "Enter" | "Escape", value: string) => void;
};

export function SuggestInput({ value, onChange, options, commaList = false, onPick, onKey, ...input }: Props) {
  const combobox = useCombobox({ onDropdownClose: () => combobox.resetSelectedOption() });
  const shown = useMemo(() => suggestions(options, value, commaList), [options, value, commaList]);
  const keyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    const choosing = combobox.dropdownOpened && combobox.getSelectedOptionIndex() !== -1;
    if (e.key === "Enter" && !choosing) onKey?.("Enter", value);
    if (e.key === "Escape") onKey?.("Escape", value);
  };
  return (
    <Combobox
      store={combobox}
      onOptionSubmit={(option) => {
        const next = applySuggestion(value, option, commaList);
        onChange(next);
        combobox.closeDropdown();
        onPick?.(next);
      }}
    >
      <Combobox.Target>
        <TextInput
          {...input}
          value={value}
          onChange={(e) => {
            onChange(e.currentTarget.value);
            combobox.openDropdown();
            combobox.resetSelectedOption();
          }}
          onFocus={(e) => {
            combobox.openDropdown();
            input.onFocus?.(e);
          }}
          onClick={(e) => {
            combobox.openDropdown();
            input.onClick?.(e);
          }}
          onBlur={(e) => {
            combobox.closeDropdown();
            input.onBlur?.(e);
          }}
          onKeyDown={keyDown}
          autoComplete="off"
        />
      </Combobox.Target>
      <Combobox.Dropdown hidden={shown.length === 0}>
        <Combobox.Options mah={260} style={{ overflowY: "auto" }}>
          {shown.map((o) => (
            <Combobox.Option value={o} key={o}>
              {o}
            </Combobox.Option>
          ))}
        </Combobox.Options>
      </Combobox.Dropdown>
    </Combobox>
  );
}
