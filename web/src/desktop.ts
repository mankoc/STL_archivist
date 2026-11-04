// The StlArchivist desktop shell (rust/stl-desktop): when the web client runs in
// its window, the shell lets this page show folders in the file manager, open
// archives in MeshPeek or an archive program, and run a compute worker for this server. Those are the
// only shell commands a server's page may call.

import type { ComponentsInfo } from "./jobs/components";

type Invoke =(cmd: string, args?: Record<string, unknown>) => Promise<unknown>;

function shellInvoke(): Invoke | null {
  const internals = (window as unknown as { __TAURI_INTERNALS__?: { invoke?: Invoke } }).__TAURI_INTERNALS__;
  return typeof internals?.invoke === "function" ? internals.invoke : null;
}

/** Whether the page runs inside the desktop shell. */
export function inDesktopShell(): boolean {
  return shellInvoke() != null;
}

/** Show the folder holding `path` (an archive, or a folder) in the file manager. */
export async function openFolder(path: string): Promise<void> {
  const invoke = shellInvoke();
  if (!invoke) throw new Error("only the desktop app can open folders");
  await invoke("open_folder", { path });
}

/** A program on this computer (MeshPeek, the archive program): the one chosen in Settings, and the one the shell runs. */
export interface ProgramState {
  chosen: string | null;
  /** null: no program (the chosen one is missing, or none was found). */
  program: string | null;
}
export type MeshPeekState = ProgramState;

/** A shell command's state; null outside the desktop app, or in an older one without it. */
async function programState(cmd: string): Promise<ProgramState | null> {
  const invoke = shellInvoke();
  if (!invoke) return null;
  try {
    return (await invoke(cmd)) as ProgramState;
  } catch {
    return null;
  }
}

/** MeshPeek on this computer; null outside the desktop app, or in an older one. */
export const meshpeekState = () => programState("meshpeek_state");

/** Choose MeshPeek's program in the shell's file dialog; null when cancelled. */
export const pickMeshPeek = () => shellCall<MeshPeekState | null>("pick_meshpeek");
/** Forget the chosen program: the shell looks for MeshPeek again. */
export const clearMeshPeek = () => shellCall<MeshPeekState>("clear_meshpeek");

/** Open an archive (`path`, as this computer sees it) in MeshPeek, or one model file inside it (`entry`). */
export const openInMeshPeek = (path: string, entry?: string) =>
  shellCall<void>("open_in_meshpeek", { path, entry: entry ?? null });

/** The archive program (PeaZip, 7-Zip, …) on this computer; null outside the desktop app, or in an older one. */
export const packerState = () => programState("packer_state");
/** Choose the archive program in the shell's file dialog; null when cancelled. */
export const pickPacker = () => shellCall<ProgramState | null>("pick_packer");
/** Forget the chosen program: the shell looks for PeaZip or 7-Zip again. */
export const clearPacker = () => shellCall<ProgramState>("clear_packer");
/** Open an archive (`path`, as this computer sees it) in the archive program. */
export const openInPacker = (path: string) => shellCall<void>("open_in_packer", { path });
/** Ask the shell to download and run `version`'s installer (it confirms, and closes the app). */
export const installUpdate = (version: string) => shellCall<void>("install_update", { version });

/** The desktop app's compute worker, as this server's page sees it. */
export interface WorkerState {
  /** A worker runs for this server. */
  running: boolean;
  /** The server a worker runs for, when it is another one. */
  other_server: string | null;
  /** Where its CLIP encoder and AI model come from. */
  data_dir: string;
  /** Why it stopped (exit status and last log lines), when it did. */
  stopped: string | null;
}

async function workerCall(cmd: string, args?: Record<string, unknown>): Promise<WorkerState> {
  const invoke = shellInvoke();
  if (!invoke) throw new Error("only the desktop app can run a compute worker");
  return (await invoke(cmd, args)) as WorkerState;
}

/** The components in the worker's folder on this computer. */
export interface WorkerComponents {
  data_dir: string;
  info: ComponentsInfo;
}

/** The last install or removal in the worker's folder. */
export interface InstallState {
  running: boolean;
  /** Bytes done and in all. */
  progress: [number, number] | null;
  log: string[];
  done: string | null;
  error: string | null;
}

async function shellCall<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const invoke = shellInvoke();
  if (!invoke) throw new Error(`only the desktop app can do this (${cmd})`);
  return (await invoke(cmd, args)) as T;
}

export const workerComponents = () => shellCall<WorkerComponents>("worker_components");
export const changeWorkerComponents = (action: "install" | "remove", components: string[], variants: Record<string, string> = {}) =>
  shellCall<InstallState>("change_worker_components", { action, components, variants });
export const workerInstallState = () => shellCall<InstallState>("worker_install_state");
/** Choose the worker's folder (a dialog in the shell); null when cancelled. */
export const pickWorkerFolder = () => shellCall<string | null>("pick_worker_folder");

export const workerState = () => workerCall("worker_state");
/** Run a worker for this server with `token` (from `POST /api/workers/token`). */
export const startWorker = (token: string) => workerCall("start_worker", { token });
export const stopWorker = () => workerCall("stop_worker");
