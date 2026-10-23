//! Optional components (administrators): what is installed. Installing and
//! removing are the `install_components` / `remove_components` jobs.

use axum::Json;
use axum::extract::State;

use crate::accounts::Role;
use crate::auth::CurrentUser;
use crate::components::ComponentsInfo;
use crate::{ApiError, AppState};

/// The downloadable components and whether each is installed.
#[utoipa::path(get, path = "/api/components", tag = "components",
    responses((status = 200, body = ComponentsInfo), (status = 403, body = crate::error::ErrorBody)))]
pub async fn status(State(lib): State<AppState>, me: CurrentUser) -> Result<Json<ComponentsInfo>, ApiError> {
    me.require(Role::Admin)?;
    Ok(Json(lib.run(|lib| Ok::<_, ApiError>(crate::components::status(&lib.place()))).await?))
}
