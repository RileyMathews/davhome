use askama::Template;
use axum::{body::Bytes, extract::Path, extract::State, http::HeaderMap, http::StatusCode};
use sqlx::PgPool;

use crate::{auth, models::calendar};

use super::{
    RequestedProps, calendar_collection_href, calendar_home_href, parse_propfind_request,
    principal_href,
    response::{DavResponse, RenderedDavProperty, propfind_response},
};

const HOME_ALLPROP: RequestedProps = RequestedProps {
    current_user_principal: true,
    principal_url: true,
    calendar_home_set: true,
    resourcetype: true,
    displayname: true,
    supported_calendar_component_set: false,
};

const CHILD_CALENDAR_ALLPROP: RequestedProps = RequestedProps {
    current_user_principal: true,
    principal_url: true,
    calendar_home_set: false,
    resourcetype: true,
    displayname: true,
    supported_calendar_component_set: true,
};

#[derive(Template)]
#[template(path = "dav/propfind_calendar_home.xml")]
struct CalendarHomePropfindTemplate {
    home: CalendarHomePropfindResponse,
    calendars: Vec<CalendarCollectionPropfindResponse>,
}

struct CalendarHomePropfindResponse {
    href: String,
    properties: Vec<RenderedDavProperty>,
}

struct CalendarCollectionPropfindResponse {
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

pub async fn handle_home_options() -> DavResponse {
    DavResponse::new(StatusCode::NO_CONTENT)
}

pub async fn handle_home_propfind(
    State(pool): State<PgPool>,
    headers: HeaderMap,
    Path(username): Path<String>,
    body: Bytes,
) -> DavResponse {
    let user = match auth::require_dav_basic_auth(&pool, &headers).await {
        Ok(user) => user,
        Err(response) => return response,
    };

    if user.username != username {
        return DavResponse::new(StatusCode::FORBIDDEN);
    }

    let request = parse_propfind_request(&body);
    let props = request.requested_props(HOME_ALLPROP);
    let mut calendars = Vec::new();

    if propfind_depth(&headers) == 1 {
        let calendar_props = request.requested_props(CHILD_CALENDAR_ALLPROP);
        let bindings = match calendar::list_dav_calendar_bindings(&pool, user.id).await {
            Ok(bindings) => bindings,
            Err(_) => return DavResponse::new(StatusCode::INTERNAL_SERVER_ERROR),
        };

        calendars.extend(
            bindings
                .into_iter()
                .map(|binding| child_calendar_response(&calendar_props, &username, binding)),
        );
    }

    propfind_response(CalendarHomePropfindTemplate {
        home: CalendarHomePropfindResponse {
            href: calendar_home_href(&username),
            properties: home_properties(&props, &username),
        },
        calendars,
    })
}

pub async fn handle_home_fallback() -> DavResponse {
    DavResponse::new(StatusCode::NOT_FOUND)
}

fn propfind_depth(headers: &HeaderMap) -> u8 {
    match headers.get("Depth").and_then(|value| value.to_str().ok()) {
        Some("1") => 1,
        _ => 0,
    }
}

fn home_properties(props: &RequestedProps, username: &str) -> Vec<RenderedDavProperty> {
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
            displayname: username.to_string(),
        }));
    }

    properties
}

fn child_calendar_response(
    props: &RequestedProps,
    username: &str,
    binding: calendar::DavCalendarBinding,
) -> CalendarCollectionPropfindResponse {
    let href = calendar_collection_href(username, &binding.uri);
    CalendarCollectionPropfindResponse {
        href,
        properties: child_calendar_properties(props, username, binding),
    }
}

fn child_calendar_properties(
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
