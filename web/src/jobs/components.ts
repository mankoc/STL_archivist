// Optional components (Maintenance → AI and add-on components): what a choice
// downloads, and the defaults.
import type { Schemas } from "../api/client";

export type ComponentsInfo = Schemas["ComponentsInfo"];
export type ComponentStatus = Schemas["ComponentStatus"];

/** The variant to offer per component with variants (the server's default for the GPUs found). */
export function defaultVariants(info: ComponentsInfo): Record<string, string> {
  return Object.fromEntries(
    info.components.filter((c) => c.variants.length > 0).map((c) => [c.id, c.default_variant ?? c.variants[0]]),
  );
}

/** How a variant is shown. */
export function variantLabel(v: string): string {
  return { cpu: "CPU", cuda: "NVIDIA GPU (CUDA)", directml: "GPU (DirectML)" }[v] ?? v;
}

/** Download size of a component in `variant` (ignored without variants). */
export function sizeOf(c: ComponentStatus, variant: string | undefined): number {
  const s = c.sizes.find((x) => x.variant === variant) ?? c.sizes.find((x) => x.variant === "default") ?? c.sizes[0];
  return s?.bytes ?? 0;
}

/** Whether a component can be installed: missing, or installed in another variant. */
export function installable(c: ComponentStatus, variant: string | undefined): boolean {
  return c.state === "missing" || (c.state === "installed" && c.variants.length > 0 && c.variant !== variant);
}

/** Total download of the chosen components, each in its variant. */
export function totalBytes(components: ComponentStatus[], chosen: string[], variants: Record<string, string>): number {
  return components
    .filter((c) => chosen.includes(c.id) && installable(c, variants[c.id]))
    .reduce((sum, c) => sum + sizeOf(c, variants[c.id]), 0);
}

/** Components whose licence must be accepted before downloading. */
export const NEEDS_ACCEPTANCE = ["ai_model"];
