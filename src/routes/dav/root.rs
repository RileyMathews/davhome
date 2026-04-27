use askama::Template;
use axum::{body::Bytes, extract::State, http::HeaderMap, http::StatusCode};
use sqlx::PgPool;

use crate::auth;

use super::{
    RequestedProps, calendar_home_href, parse_propfind_request, principal_href,
    response::{DavResponse, RenderedDavProperty, propfind_response},
};

const ROOT_ALLPROP: RequestedProps = RequestedProps {
    current_user_principal: true,
    principal_url: true,
    calendar_home_set: true,
    resourcetype: true,
    displayname: true,
    supported_calendar_component_set: false,
};

#[derive(Template)]
#[template(path = "dav/propfind_root.xml")]
struct RootPropfindTemplate {
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

pub async fn handle_root_options() -> DavResponse {
    DavResponse::new(StatusCode::NO_CONTENT)
}

pub async fn handle_root_propfind(
    State(pool): State<PgPool>,
    headers: HeaderMap,
    body: Bytes,
) -> DavResponse {
    let user = match auth::require_dav_basic_auth(&pool, &headers).await {
        Ok(user) => user,
        Err(response) => return response,
    };

    let request = parse_propfind_request(&body);
    let props = request.requested_props(ROOT_ALLPROP);

    propfind_response(RootPropfindTemplate {
        href: "/dav/".to_string(),
        properties: root_properties(&props, &user.username),
    })
}

pub async fn handle_root_fallback() -> DavResponse {
    DavResponse::new(StatusCode::NOT_FOUND)
}

fn root_properties(props: &RequestedProps, username: &str) -> Vec<RenderedDavProperty> {
    let mut properties = Vec::new();
    let principal_href = principal_href(username);

    if props.current_user_principal {
        properties.push(RenderedDavProperty::new(
            CurrentUserPrincipalPropertyTemplate {
                href: principal_href.clone(),
            },
        ));
    }
    if props.principal_url {
        properties.push(RenderedDavProperty::new(PrincipalUrlPropertyTemplate {
            href: principal_href,
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
            calendar: false,
        }));
    }
    if props.displayname {
        properties.push(RenderedDavProperty::new(DisplaynamePropertyTemplate {
            displayname: "davhome".to_string(),
        }));
    }

    properties
}
