//! Child processes that must not outlive their parent: the desktop shell's local
//! `stl-server` (it holds the library lock) and the server's `llama-server`.
//! Killing a child on drop covers a normal exit; this covers a crash or a forced
//! kill of the parent too.
//!
//! On Windows the child joins a job object that kills its processes when the
//! last handle to it closes, which happens when this process ends. Elsewhere
//! this does nothing (a Linux server runs under a service manager that stops
//! the process group).

use std::process::Child;

/// Make `child` end when this process ends. Best effort: returns whether the
/// child was attached.
pub fn end_with_parent(child: &Child) -> bool {
    imp::attach(child)
}

#[cfg(windows)]
mod imp {
    use std::os::windows::io::AsRawHandle;
    use std::process::Child;
    use std::sync::OnceLock;

    use windows_sys::Win32::Foundation::HANDLE;
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JobObjectExtendedLimitInformation, SetInformationJobObject,
    };

    /// The job, created once and never closed: the system closes it with the process.
    struct Job(HANDLE);
    // SAFETY: a job handle may be used from any thread.
    unsafe impl Send for Job {}
    unsafe impl Sync for Job {}

    fn job() -> Option<&'static Job> {
        static JOB: OnceLock<Option<Job>> = OnceLock::new();
        JOB.get_or_init(|| {
            // SAFETY: plain Win32 calls on a handle this function owns; the
            // information struct is zero-initialised and sized as the call expects.
            unsafe {
                let h = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if h.is_null() {
                    return None;
                }
                let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                let ok = SetInformationJobObject(
                    h,
                    JobObjectExtendedLimitInformation,
                    (&raw const info).cast(),
                    std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                );
                (ok != 0).then_some(Job(h))
            }
        })
        .as_ref()
    }

    pub fn attach(child: &Child) -> bool {
        let Some(job) = job() else { return false };
        // SAFETY: both handles are valid for the duration of the call.
        unsafe { AssignProcessToJobObject(job.0, child.as_raw_handle() as HANDLE) != 0 }
    }
}

#[cfg(not(windows))]
mod imp {
    pub fn attach(_child: &std::process::Child) -> bool {
        false
    }
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;

    #[test]
    fn a_child_joins_the_job() {
        let mut child = std::process::Command::new("cmd").args(["/C", "ping -n 3 127.0.0.1 >NUL"]).spawn().unwrap();
        assert!(end_with_parent(&child));
        let _ = child.kill();
        let _ = child.wait();
    }
}
