// The stlLibrary desktop shell (rust/stl-desktop): when the web client runs in
// its window, the shell lets this page show folders in the file manager. That
// is the only shell command a server's page may call.

type Invoke = (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;

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
