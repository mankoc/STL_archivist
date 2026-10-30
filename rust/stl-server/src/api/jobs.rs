//! Jobs (crawls, maintenance), crawled folders, and the event stream
//! (`/api/events`, a WebSocket of [`ServerEvent`] JSON frames).

use axum::Json;
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::Response;
use serde::Serialize;
use tokio::sync::broadcast::error::RecvError;
use utoipa::ToSchema;

use crate::accounts::Role;
use crate::auth::CurrentUser;
use crate::jobs::{JobInfo, JobRequest, ServerEvent};
use crate::{ApiError, AppState};

/// Queued, running and recently finished jobs, oldest first.
#[utoipa::path(get, path = "/api/jobs", tag = "jobs", responses((status = 200, body = Vec<JobInfo>)))]
pub async fn list(State(lib): State<AppState>, _me: CurrentUser) -> Json<Vec<JobInfo>> {
    Json(lib.jobs.list())
}

/// Queue a job. Jobs run one at a time, in order. Needs the editor role
/// (compacting the database, backups, imports and syncs: admin).
#[utoipa::path(post, path = "/api/jobs", tag = "jobs", request_body = JobRequest,
    responses((status = 202, body = JobInfo), (status = 403, body = crate::error::ErrorBody)))]
pub async fn submit(
    State(lib): State<AppState>,
    me: CurrentUser,
    Json(mut request): Json<JobRequest>,
) -> Result<(StatusCode, Json<JobInfo>), ApiError> {
    let admin = matches!(
        request,
        JobRequest::CompactDatabase
            | JobRequest::Backup
            | JobRequest::ImportLibrary { .. }
            | JobRequest::Sync { .. }
            | JobRequest::SyncExport { .. }
            | JobRequest::ClearLibrary { .. }
            | JobRequest::InstallComponents { .. }
            | JobRequest::RemoveComponents { .. }
    );
    me.require(if admin { Role::Admin } else { Role::Editor })?;
    request.validate().map_err(ApiError::bad_request)?;
    // A searched folder is read and records may point into it: the same access as a crawl.
    if let JobRequest::SearchFolder { folder, .. }
    | JobRequest::SortFiles { target_dir: folder, .. }
    | JobRequest::RelocateLibrary { root: folder, .. } = &request
    {
        let (lib2, me2, path) = (std::sync::Arc::clone(&lib), me.clone(), std::path::PathBuf::from(folder));
        lib2.run(move |lib| super::files::check_access(lib, &me2, &path)).await?;
    }
    if let JobRequest::UpdateFolders { folders } = &request {
        for folder in folders.clone() {
            let (lib2, me2) = (std::sync::Arc::clone(&lib), me.clone());
            lib2.run(move |lib| super::files::check_access(lib, &me2, &std::path::PathBuf::from(folder))).await?;
        }
    }
    if let JobRequest::Crawl { folder, .. } = &mut request {
        let (lib2, me2, path) = (std::sync::Arc::clone(&lib), me.clone(), std::path::PathBuf::from(&*folder));
        let known = lib2
            .run(move |lib| {
                super::files::check_access(lib, &me2, &path)?;
                super::files::known_spelling(lib, &path)
            })
            .await?;
        // Crawl a known folder under its stored spelling (`C:/STL` = `C:\STL`).
        if let Some(known) = known {
            *folder = known;
        }
    }
    let info = lib.submit_job(request, &me.user.name);
    tracing::info!(job = info.id, kind = info.kind, user = %me.user.name, "job queued");
    Ok((StatusCode::ACCEPTED, Json(info)))
}

#[derive(Serialize, ToSchema)]
pub struct JobDetail {
    pub job: JobInfo,
    /// The job's log, oldest line first (at most the last 2,000 lines).
    pub log: Vec<String>,
    /// What a duplicate finder found, once it is done (`pairs` or `groups`).
    pub result: Option<crate::dupes::JobResult>,
    /// The archives a crawl added, updated and deleted (for jobs with `counts`).
    pub changes: Option<crate::jobs::JobChanges>,
}

/// One job with its log.
#[utoipa::path(get, path = "/api/jobs/{id}", tag = "jobs", params(("id" = u64, Path, description = "Job id")),
    responses((status = 200, body = JobDetail), (status = 404, body = crate::error::ErrorBody)))]
pub async fn detail(State(lib): State<AppState>, _me: CurrentUser, Path(id): Path<u64>) -> Result<Json<JobDetail>, ApiError> {
    let job = lib.jobs.get(id).ok_or_else(|| ApiError::not_found(format!("no job {id}")))?;
    Ok(Json(JobDetail { job: job.info(), log: job.log(), result: job.result(), changes: job.changes() }))
}

/// Cancel a job: a queued one at once, a running one at its next checkpoint
/// (what it finished so far stays). 409 when it has already finished.
#[utoipa::path(delete, path = "/api/jobs/{id}", tag = "jobs", params(("id" = u64, Path, description = "Job id")),
    responses((status = 202), (status = 404, body = crate::error::ErrorBody), (status = 409, body = crate::error::ErrorBody)))]
pub async fn cancel(State(lib): State<AppState>, me: CurrentUser, Path(id): Path<u64>) -> Result<StatusCode, ApiError> {
    me.require(Role::Editor)?;
    let job = lib.jobs.get(id).ok_or_else(|| ApiError::not_found(format!("no job {id}")))?;
    if lib.jobs.cancel(&job) {
        Ok(StatusCode::ACCEPTED)
    } else {
        Err(ApiError::new(StatusCode::CONFLICT, "the job has already finished"))
    }
}

/// The event stream: [`ServerEvent`] JSON text frames (job progress and logs,
/// library changes). Authenticates like any route (the session cookie works
/// for browsers, which cannot set headers on a WebSocket).
#[utoipa::path(get, path = "/api/events", tag = "jobs",
    responses((status = 101, description = "WebSocket of ServerEvent frames", body = ServerEvent)))]
pub async fn events(State(lib): State<AppState>, _me: CurrentUser, ws: WebSocketUpgrade) -> Response {
    let rx = lib.jobs.events.subscribe();
    ws.on_upgrade(move |socket| stream(socket, rx))
}

async fn stream(mut socket: WebSocket, mut rx: tokio::sync::broadcast::Receiver<ServerEvent>) {
    loop {
        tokio::select! {
            event = rx.recv() => {
                let event = match event {
                    Ok(e) => e,
                    Err(RecvError::Lagged(_)) => ServerEvent::Resync,
                    Err(RecvError::Closed) => return,
                };
                let text = serde_json::to_string(&event).unwrap_or_default();
                if socket.send(Message::Text(text.into())).await.is_err() {
                    return;
                }
            }
            incoming = socket.recv() => match incoming {
                // Clients only listen; pings are answered by axum.
                Some(Ok(Message::Close(_))) | None | Some(Err(_)) => return,
                Some(Ok(_)) => {}
            }
        }
    }
}

