// Optional components (Maintenance → AI and add-on components): what a choice
// downloads, and the defaults.
import type { Schemas } from "../api/client";

export type ComponentsInfo = Schemas["ComponentsInfo"];
export type ComponentStatus = Schemas["ComponentStatus"];

/** The AI runtime's variant to offer first: CUDA when an NVIDIA GPU is present. */
export function defaultVariant(info: ComponentsInfo): string {
  if (info.nvidia_gpu && info.variants.includes("cuda")) return "cuda";
  return info.variants[0] ?? "cpu";
}

/** Download size of a component (the AI runtime's in `variant`). */
export function sizeOf(c: ComponentStatus, variant: string): number {
  const s = c.sizes.find((x) => x.variant === variant) ?? c.sizes.find((x) => x.variant === "default") ?? c.sizes[0];
  return s?.bytes ?? 0;
}

/** Whether a component can be installed: missing, or the AI runtime in another variant. */
export function installable(c: ComponentStatus, variant: string): boolean {
  return c.state === "missing" || (c.id === "ai_runtime" && c.state === "installed" && c.variant !== variant);
}

/** Total download of the chosen components. */
export function totalBytes(components: ComponentStatus[], chosen: string[], variant: string): number {
  return components.filter((c) => chosen.includes(c.id) && installable(c, variant)).reduce((sum, c) => sum + sizeOf(c, variant), 0);
}

/** Components whose licence must be accepted before downloading. */
export const NEEDS_ACCEPTANCE = ["ai_model"];
