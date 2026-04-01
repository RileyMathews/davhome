use askama::Template;
use axum::{
    extract::{Path, State},
    http::{HeaderMap, HeaderValue, StatusCode, header},
    response::{IntoResponse, Response},
};
use sqlx::PgPool;

use crate::{auth, models::calendar};

#[derive(Template)]
#[template(path = "dav/empty.xml", escape = "none")]
struct EmptyDavTemplate;

pub struct DavResponse {
    status: StatusCode,
    headers: HeaderMap,
}

impl DavResponse {
    pub fn new(status: StatusCode) -> Self {
        Self {
            status,
            headers: HeaderMap::new(),
        }
    }

    pub fn with_header(mut self, name: header::HeaderName, value: HeaderValue) -> Self {
        self.headers.insert(name, value);
        self
    }
}

impl IntoResponse for DavResponse {
    fn into_response(self) -> Response {
        let body = EmptyDavTemplate.render().unwrap();
        let mut response = (self.status, body).into_response();

        response.headers_mut().insert(
            header::CONTENT_TYPE,
            HeaderValue::from_static("application/xml; charset=utf-8"),
        );

        for (name, value) in &self.headers {
            response.headers_mut().insert(name, value.clone());
        }

        response
    }
}

pub async fn handle_root_options() -> DavResponse {
    DavResponse::new(StatusCode::NO_CONTENT)
}

pub async fn handle_root_fallback() -> DavResponse {
    DavResponse::new(StatusCode::NOT_FOUND)
}

pub async fn handle_home_options() -> DavResponse {
    DavResponse::new(StatusCode::NO_CONTENT)
}

pub async fn handle_home_fallback() -> DavResponse {
    DavResponse::new(StatusCode::NOT_FOUND)
}

pub async fn handle_collection_options() -> DavResponse {
    DavResponse::new(StatusCode::NO_CONTENT)
}

pub async fn handle_collection_delete(
    State(pool): State<PgPool>,
    headers: HeaderMap,
    Path((username, binding)): Path<(String, String)>,
) -> DavResponse {
    let user = match auth::require_dav_basic_auth(&pool, &headers).await {
        Ok(user) => user,
        Err(response) => return response,
    };

    if user.username != username {
        return DavResponse::new(StatusCode::FORBIDDEN);
    }

    match calendar::delete_calendar_by_uri_if_owner(&pool, user.id, &binding).await {
        Ok(true) => DavResponse::new(StatusCode::NO_CONTENT),
        Ok(false) => DavResponse::new(StatusCode::NOT_FOUND),
        Err(_) => DavResponse::new(StatusCode::INTERNAL_SERVER_ERROR),
    }
}

pub async fn handle_collection_mkcol(
    State(pool): State<PgPool>,
    headers: HeaderMap,
    Path((username, binding)): Path<(String, String)>,
) -> DavResponse {
    let user = match auth::require_dav_basic_auth(&pool, &headers).await {
        Ok(user) => user,
        Err(response) => return response,
    };

    if user.username != username {
        return DavResponse::new(StatusCode::FORBIDDEN);
    }

    // This is intentionally a temporary shortcut: plain WebDAV MKCOL creates a
    // generic collection, while CalDAV calendar collections are a more specific
    // resource type typically created with MKCALENDAR. For the current minimal
    // DAV slice we map MKCOL directly to calendar creation so the basic client
    // compatibility tests can progress. Longer term we likely want a top-level
    // collection model in the namespace layer, with calendars represented as a
    // distinct typed resource nested under those generic collections.
    match calendar::create_calendar(&pool, user.id, &binding, &binding, None).await {
        Ok(_) => DavResponse::new(StatusCode::CREATED),
        Err(sqlx::Error::Database(db_err)) if db_err.is_unique_violation() => {
            DavResponse::new(StatusCode::METHOD_NOT_ALLOWED)
        }
        Err(_) => DavResponse::new(StatusCode::INTERNAL_SERVER_ERROR),
    }
}
