use askama::Template;
use axum::{body::Bytes, extract::Path, extract::State, http::HeaderMap, http::StatusCode};
use sqlx::PgPool;

use crate::{auth, models::calendar};

use super::{
    RequestedProps, calendar_collection_href, calendar_home_href, parse_propfind_request,
    principal_href,
    response::{DavResponse, RenderedDavProperty, propfind_response},
};

const COLLECTION_ALLPROP: RequestedProps = RequestedProps {
    current_user_principal: true,
    principal_url: true,
    calendar_home_set: false,
    resourcetype: true,
    displayname: true,
    supported_calendar_component_set: true,
};

#[derive(Template)]
#[template(path = "dav/propfind_calendar_collection.xml")]
struct CalendarCollectionPropfindTemplate {
    href: String,
    properties: Vec<RenderedDavProperty>,
}

#[derive(Template)]
#[template(path = "dav/properties/current_user_principal.xml")]
struct CurrentUserPrincipalPropertyTemplate {
    href: String,
}

#[derive(Template)]
#[template(path = "dav/properties/principal_url.xml")]
struct PrincipalUrlPropertyTemplate {
    href: String,
}

#[derive(Template)]
#[template(path = "dav/properties/calendar_home_set.xml")]
struct CalendarHomeSetPropertyTemplate {
    href: String,
}

#[derive(Template)]
#[template(path = "dav/properties/resourcetype.xml")]
struct ResourcetypePropertyTemplate {
    collection: bool,
    calendar: bool,
}

#[derive(Template)]
#[template(path = "dav/properties/displayname.xml")]
struct DisplaynamePropertyTemplate {
    displayname: String,
}

#[derive(Template)]
#[template(path = "dav/properties/supported_calendar_component_set.xml")]
struct SupportedCalendarComponentSetPropertyTemplate {
    components: Vec<String>,
}

pub async fn handle_collection_options() -> DavResponse {
    DavResponse::new(StatusCode::NO_CONTENT)
}

pub async fn handle_collection_propfind(
    State(pool): State<PgPool>,
    headers: HeaderMap,
    Path((username, binding)): Path<(String, String)>,
    body: Bytes,
) -> DavResponse {
    let user = match auth::require_dav_basic_auth(&pool, &headers).await {
        Ok(user) => user,
        Err(response) => return response,
    };

    if user.username != username {
        return DavResponse::new(StatusCode::FORBIDDEN);
    }

    let binding = match calendar::find_dav_calendar_binding(&pool, user.id, &binding).await {
        Ok(Some(binding)) => binding,
        Ok(None) => return DavResponse::new(StatusCode::NOT_FOUND),
        Err(_) => return DavResponse::new(StatusCode::INTERNAL_SERVER_ERROR),
    };

    let request = parse_propfind_request(&body);
    let props = request.requested_props(COLLECTION_ALLPROP);
    let href = calendar_collection_href(&username, &binding.uri);

    propfind_response(CalendarCollectionPropfindTemplate {
        href,
        properties: collection_properties(&props, &username, binding),
    })
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

fn collection_properties(
    props: &RequestedProps,
    username: &str,
    binding: calendar::DavCalendarBinding,
) -> Vec<RenderedDavProperty> {
    let mut properties = Vec::new();

    if props.current_user_principal {
        properties.push(RenderedDavProperty::new(
            CurrentUserPrincipalPropertyTemplate {
                href: principal_href(username),
            },
        ));
    }
    if props.principal_url {
        properties.push(RenderedDavProperty::new(PrincipalUrlPropertyTemplate {
            href: principal_href(username),
        }));
    }
    if props.calendar_home_set {
        properties.push(RenderedDavProperty::new(CalendarHomeSetPropertyTemplate {
            href: calendar_home_href(username),
        }));
    }
    if props.resourcetype {
        properties.push(RenderedDavProperty::new(ResourcetypePropertyTemplate {
            collection: true,
            calendar: true,
        }));
    }
    if props.displayname {
        properties.push(RenderedDavProperty::new(DisplaynamePropertyTemplate {
            displayname: binding
                .displayname
                .clone()
                .unwrap_or_else(|| binding.uri.clone()),
        }));
    }
    if props.supported_calendar_component_set {
        properties.push(RenderedDavProperty::new(
            SupportedCalendarComponentSetPropertyTemplate {
                components: binding.supported_component_set,
            },
        ));
    }

    properties
}
