// Optional components (Settings → Components): what a choice downloads, the
// defaults, and whether this computer's worker shares the server's folder.
import type { Schemas } from "../api/client";
import { pathKey } from "./libraries";

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
  return (
    {
      cpu: "CPU",
      cuda: "NVIDIA GPU (CUDA)",
      directml: "GPU (DirectML)",
      "qwen3-vl-4b": "Qwen3-VL 4B",
      "qwen2.5-vl-3b": "Qwen2.5-VL 3B",
    }[v] ?? v
  );
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

/** Whether a component's licence must be accepted before downloading it in `variant`
 *  (the Qwen Research License of Qwen2.5-VL; Qwen3-VL is Apache-2.0). */
export function needsAcceptance(id: string, variant: string | undefined): boolean {
  return id === "ai_model" && variant === "qwen2.5-vl-3b";
}

/** Whether the compute worker's folder on this computer is the server's data
 * directory (a local library): then one list of components serves both. */
export function sameFolder(serverDir: string | undefined, workerDir: string | undefined): boolean {
  return !!serverDir && !!workerDir && pathKey(serverDir) === pathKey(workerDir);
}
