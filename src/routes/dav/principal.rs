use askama::Template;
use axum::{body::Bytes, extract::Path, extract::State, http::HeaderMap, http::StatusCode};
use sqlx::PgPool;

use crate::{auth, models::user};

use super::{
    RequestedProps, calendar_home_href, parse_propfind_request, principal_href,
    principal_uid_matches_user,
    response::{DavResponse, RenderedDavProperty, propfind_response},
};

const PRINCIPAL_ALLPROP: RequestedProps = RequestedProps {
    current_user_principal: true,
    principal_url: true,
    calendar_home_set: true,
    resourcetype: true,
    displayname: true,
    supported_calendar_component_set: false,
    supported_report_set: false,
    sync_token: false,
    getetag: false,
    getlastmodified: false,
    getcontenttype: false,
    getcontentlength: false,
};

#[derive(Template)]
#[template(path = "dav/propfind_root.xml")]
struct PrincipalPropfindTemplate {
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

pub async fn handle_principal_collection_options() -> DavResponse {
    DavResponse::new(StatusCode::NO_CONTENT)
}

pub async fn handle_principal_collection_propfind(
    State(pool): State<PgPool>,
    headers: HeaderMap,
    body: Bytes,
) -> DavResponse {
    let user = match auth::require_dav_basic_auth(&pool, &headers).await {
        Ok(user) => user,
        Err(response) => return response,
    };

    principal_propfind_response(&user, "/dav/principals/users/".to_string(), body)
}

pub async fn handle_principal_options() -> DavResponse {
    DavResponse::new(StatusCode::NO_CONTENT)
}

pub async fn handle_principal_propfind(
    State(pool): State<PgPool>,
    headers: HeaderMap,
    Path(principal_uid): Path<String>,
    body: Bytes,
) -> DavResponse {
    let user = match auth::require_dav_basic_auth(&pool, &headers).await {
        Ok(user) => user,
        Err(response) => return response,
    };

    if !principal_uid_matches_user(&principal_uid, &user) {
        return DavResponse::new(StatusCode::FORBIDDEN);
    }

    principal_propfind_response(&user, principal_href(&user), body)
}

fn principal_propfind_response(user: &user::User, href: String, body: Bytes) -> DavResponse {
    let request = parse_propfind_request(&body);
    let props = request.requested_props(PRINCIPAL_ALLPROP);

    propfind_response(PrincipalPropfindTemplate {
        href,
        properties: principal_properties(&props, user),
    })
}

fn principal_properties(props: &RequestedProps, user: &user::User) -> Vec<RenderedDavProperty> {
    let mut properties = Vec::new();

    if props.current_user_principal {
        properties.push(RenderedDavProperty::new(
            CurrentUserPrincipalPropertyTemplate {
                href: principal_href(user),
            },
        ));
    }
    if props.principal_url {
        properties.push(RenderedDavProperty::new(PrincipalUrlPropertyTemplate {
            href: principal_href(user),
        }));
    }
    if props.calendar_home_set {
        properties.push(RenderedDavProperty::new(CalendarHomeSetPropertyTemplate {
            href: calendar_home_href(user),
        }));
    }
    if props.resourcetype {
        properties.push(RenderedDavProperty::new(ResourcetypePropertyTemplate {
            collection: false,
            calendar: false,
        }));
    }
    if props.displayname {
        properties.push(RenderedDavProperty::new(DisplaynamePropertyTemplate {
            displayname: user.username.clone(),
        }));
    }

    properties
}
