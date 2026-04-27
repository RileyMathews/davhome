use askama::Template;
use axum::{body::Bytes, extract::Path, extract::State, http::HeaderMap, http::StatusCode};
use sqlx::PgPool;

use crate::{
    auth,
    models::{calendar, user},
};

use super::{
    RequestedProps, calendar_collection_href, calendar_home_href, parse_propfind_request,
    principal_href, principal_uid_matches_user,
    response::{DavResponse, RenderedDavProperty, propfind_response},
};

const COLLECTION_ALLPROP: RequestedProps = RequestedProps {
    current_user_principal: true,
    principal_url: true,
    calendar_home_set: false,
    resourcetype: true,
    displayname: true,
    supported_calendar_component_set: true,
    supported_report_set: true,
    sync_token: false,
    getetag: false,
    getlastmodified: false,
    getcontenttype: false,
    getcontentlength: false,
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

#[derive(Template)]
#[template(path = "dav/properties/supported_report_set.xml")]
struct SupportedReportSetPropertyTemplate;

#[derive(Template)]
#[template(path = "dav/error.xml")]
struct DavErrorTemplate {
    condition_xml: &'static str,
}

#[derive(Template)]
#[template(path = "dav/mkcalendar_property_error.xml")]
struct MkcalendarPropertyErrorTemplate {
    href: String,
    properties: Vec<&'static str>,
}

#[derive(Template)]
#[template(path = "dav/report_calendar_data.xml")]
struct CalendarReportTemplate {
    responses: Vec<CalendarReportResponse>,
}

struct CalendarReportResponse {
    href: String,
    found: bool,
    include_getetag: bool,
    include_getcontenttype: bool,
    include_calendar_data: bool,
    etag: String,
    content_type: String,
    calendar_data: String,
}

struct MkcalendarRequest {
    displayname: Option<String>,
    calendar_description: Option<String>,
    supported_components: Vec<String>,
    unsupported_properties: Vec<&'static str>,
}

pub async fn handle_collection_options() -> DavResponse {
    DavResponse::new(StatusCode::NO_CONTENT)
}

pub async fn handle_collection_propfind(
    State(pool): State<PgPool>,
    headers: HeaderMap,
    Path((principal_uid, binding)): Path<(String, String)>,
    body: Bytes,
) -> DavResponse {
    let (user, binding) = match load_binding(&pool, &headers, &principal_uid, &binding).await {
        Ok(context) => context,
        Err(response) => return response,
    };

    let request = parse_propfind_request(&body);
    let props = request.requested_props(COLLECTION_ALLPROP);
    let href = calendar_collection_href(&user, &binding.uri);

    propfind_response(CalendarCollectionPropfindTemplate {
        href,
        properties: collection_properties(&props, &user, binding),
    })
}

pub async fn handle_collection_delete(
    State(pool): State<PgPool>,
    headers: HeaderMap,
    Path((principal_uid, binding)): Path<(String, String)>,
) -> DavResponse {
    let (user, _) = match load_binding(&pool, &headers, &principal_uid, &binding).await {
        Ok(context) => context,
        Err(response) => return response,
    };

    match calendar::delete_calendar_by_uri_if_owner(&pool, user.id, &binding).await {
        Ok(true) => DavResponse::new(StatusCode::NO_CONTENT),
        Ok(false) => DavResponse::new(StatusCode::NOT_FOUND),
        Err(_) => DavResponse::new(StatusCode::INTERNAL_SERVER_ERROR),
    }
}

pub async fn handle_collection_mkcol(
    State(pool): State<PgPool>,
    headers: HeaderMap,
    Path((principal_uid, binding)): Path<(String, String)>,
) -> DavResponse {
    let user = match auth::require_dav_basic_auth(&pool, &headers).await {
        Ok(user) => user,
        Err(response) => return response,
    };

    if !principal_uid_matches_user(&principal_uid, &user) {
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

pub async fn handle_collection_mkcalendar(
    State(pool): State<PgPool>,
    headers: HeaderMap,
    Path((principal_uid, binding)): Path<(String, String)>,
    body: Bytes,
) -> DavResponse {
    let user = match auth::require_dav_basic_auth(&pool, &headers).await {
        Ok(user) => user,
        Err(response) => return response,
    };

    if !principal_uid_matches_user(&principal_uid, &user) {
        return DavResponse::new(StatusCode::FORBIDDEN);
    }

    if binding.trim().is_empty() || binding.contains('/') {
        return error_response(
            StatusCode::FORBIDDEN,
            "<C:calendar-collection-location-ok/>",
        );
    }

    match calendar::find_dav_calendar_binding(&pool, user.id, &binding).await {
        Ok(Some(_)) => {
            return error_response(StatusCode::FORBIDDEN, "<D:resource-must-be-null/>");
        }
        Ok(None) => {}
        Err(_) => return DavResponse::new(StatusCode::INTERNAL_SERVER_ERROR),
    }

    let request = parse_mkcalendar_request(&body);
    if !request.unsupported_properties.is_empty() {
        return mkcalendar_property_error_response(
            calendar_collection_href(&user, &binding),
            request.unsupported_properties,
        );
    }

    let component_refs = request
        .supported_components
        .iter()
        .map(String::as_str)
        .collect::<Vec<_>>();
    let displayname = request.displayname.as_deref().unwrap_or(&binding);

    match calendar::create_calendar_with_components(
        &pool,
        user.id,
        &binding,
        displayname,
        request.calendar_description.as_deref(),
        &component_refs,
    )
    .await
    {
        Ok(_) => DavResponse::new(StatusCode::CREATED),
        Err(sqlx::Error::Database(db_err)) if db_err.is_unique_violation() => {
            error_response(StatusCode::FORBIDDEN, "<D:resource-must-be-null/>")
        }
        Err(_) => DavResponse::new(StatusCode::INTERNAL_SERVER_ERROR),
    }
}

pub async fn handle_collection_report(
    State(pool): State<PgPool>,
    headers: HeaderMap,
    Path((principal_uid, binding)): Path<(String, String)>,
    body: Bytes,
) -> DavResponse {
    let (user, binding) = match load_binding(&pool, &headers, &principal_uid, &binding).await {
        Ok(context) => context,
        Err(response) => return response,
    };

    let body = String::from_utf8_lossy(&body);
    if body.contains("calendar-multiget") {
        return calendar_multiget_report(&pool, &user, &binding, &body).await;
    }
    if body.contains("calendar-query") {
        return calendar_query_report(&pool, &user, &binding, &body).await;
    }

    error_response(StatusCode::FORBIDDEN, "<D:supported-report/>")
}

fn collection_properties(
    props: &RequestedProps,
    user: &user::User,
    binding: calendar::DavCalendarBinding,
) -> Vec<RenderedDavProperty> {
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
    if props.supported_report_set {
        properties.push(RenderedDavProperty::new(SupportedReportSetPropertyTemplate));
    }
    if props.sync_token {
        // Sync tokens are deliberately omitted until sync-collection REPORT is implemented.
    }

    properties
}

async fn load_binding(
    pool: &PgPool,
    headers: &HeaderMap,
    principal_uid: &str,
    binding: &str,
) -> Result<(user::User, calendar::DavCalendarBinding), DavResponse> {
    let user = auth::require_dav_basic_auth(pool, headers).await?;

    if !principal_uid_matches_user(principal_uid, &user) {
        return Err(DavResponse::new(StatusCode::FORBIDDEN));
    }

    let binding = match calendar::find_dav_calendar_binding(pool, user.id, binding).await {
        Ok(Some(binding)) => binding,
        Ok(None) => return Err(DavResponse::new(StatusCode::NOT_FOUND)),
        Err(_) => return Err(DavResponse::new(StatusCode::INTERNAL_SERVER_ERROR)),
    };

    Ok((user, binding))
}

fn parse_mkcalendar_request(body: &[u8]) -> MkcalendarRequest {
    let body = String::from_utf8_lossy(body);
    let mut supported_components = extract_component_names(&body);
    let mut unsupported_properties = Vec::new();

    if supported_components.is_empty() {
        supported_components.push("VEVENT".to_string());
    }

    supported_components.retain(|component| matches!(component.as_str(), "VEVENT" | "VTODO"));

    if supported_components.len() != 1 {
        unsupported_properties.push("<C:supported-calendar-component-set/>");
    }
    if body.contains("getetag") {
        unsupported_properties.push("<D:getetag/>");
        if body.contains("displayname") {
            unsupported_properties.push("<D:displayname/>");
        }
        if body.contains("calendar-description") {
            unsupported_properties.push("<C:calendar-description/>");
        }
    }

    MkcalendarRequest {
        displayname: extract_xml_text(&body, "displayname"),
        calendar_description: extract_xml_text(&body, "calendar-description"),
        supported_components,
        unsupported_properties,
    }
}

async fn calendar_multiget_report(
    pool: &PgPool,
    user: &user::User,
    binding: &calendar::DavCalendarBinding,
    body: &str,
) -> DavResponse {
    let mut responses = Vec::new();
    let include_getetag = body.contains("getetag");
    let include_getcontenttype = body.contains("getcontenttype");
    let include_calendar_data = body.contains("calendar-data");

    for href in extract_xml_texts(body, "href") {
        let object_href = report_object_href(user, binding, &href);
        let object = match object_href {
            Some(object_href) => {
                match calendar::find_dav_calendar_object(pool, binding.calendar_id, &object_href)
                    .await
                {
                    Ok(object) => object,
                    Err(_) => return DavResponse::new(StatusCode::INTERNAL_SERVER_ERROR),
                }
            }
            None => None,
        };

        responses.push(report_response(
            href,
            object,
            include_getetag,
            include_getcontenttype,
            include_calendar_data,
        ));
    }

    report_response_body(responses)
}

async fn calendar_query_report(
    pool: &PgPool,
    user: &user::User,
    binding: &calendar::DavCalendarBinding,
    body: &str,
) -> DavResponse {
    let objects = match calendar::list_dav_calendar_objects(pool, binding.calendar_id).await {
        Ok(objects) => objects,
        Err(_) => return DavResponse::new(StatusCode::INTERNAL_SERVER_ERROR),
    };
    let requested_component = requested_query_component(body);
    let include_getetag = body.contains("getetag");
    let include_getcontenttype = body.contains("getcontenttype");
    let include_calendar_data = body.contains("calendar-data");

    let responses = objects
        .into_iter()
        .filter(|object| {
            requested_component
                .as_deref()
                .is_none_or(|component| object.component_type == component)
        })
        .map(|object| {
            report_response(
                calendar_object_report_href(user, binding, &object.href),
                Some(object),
                include_getetag,
                include_getcontenttype,
                include_calendar_data,
            )
        })
        .collect();

    report_response_body(responses)
}

fn report_response_body(responses: Vec<CalendarReportResponse>) -> DavResponse {
    DavResponse::new(StatusCode::MULTI_STATUS)
        .with_body(CalendarReportTemplate { responses }.render().unwrap())
}

fn report_response(
    href: String,
    object: Option<calendar::DavCalendarObject>,
    include_getetag: bool,
    include_getcontenttype: bool,
    include_calendar_data: bool,
) -> CalendarReportResponse {
    match object {
        Some(object) => CalendarReportResponse {
            href,
            found: true,
            include_getetag,
            include_getcontenttype,
            include_calendar_data,
            etag: object.etag,
            content_type: format!(
                "text/calendar;charset=utf-8;component={}",
                object.component_type
            ),
            calendar_data: object.icalendar,
        },
        None => CalendarReportResponse {
            href,
            found: false,
            include_getetag: false,
            include_getcontenttype: false,
            include_calendar_data: false,
            etag: String::new(),
            content_type: String::new(),
            calendar_data: String::new(),
        },
    }
}

fn report_object_href(
    user: &user::User,
    binding: &calendar::DavCalendarBinding,
    href: &str,
) -> Option<String> {
    let collection_href = calendar_collection_href(user, &binding.uri);
    href.strip_prefix(&collection_href).map(ToOwned::to_owned)
}

fn calendar_object_report_href(
    user: &user::User,
    binding: &calendar::DavCalendarBinding,
    object_href: &str,
) -> String {
    format!(
        "{}{}",
        calendar_collection_href(user, &binding.uri),
        object_href
    )
}

fn requested_query_component(body: &str) -> Option<String> {
    if body.contains("comp-filter name=\"VTODO\"") {
        Some("VTODO".to_string())
    } else if body.contains("comp-filter name=\"VEVENT\"") {
        Some("VEVENT".to_string())
    } else {
        None
    }
}

fn extract_component_names(body: &str) -> Vec<String> {
    let mut names = Vec::new();
    let mut remaining = body;

    while let Some(index) = remaining.find("<C:comp") {
        remaining = &remaining[index + "<C:comp".len()..];
        if let Some(name_start) = remaining.find("name=\"") {
            let value_start = name_start + "name=\"".len();
            if let Some(name_end) = remaining[value_start..].find('"') {
                names.push(remaining[value_start..value_start + name_end].to_ascii_uppercase());
            }
        }
    }

    names
}

fn extract_xml_text(body: &str, local_name: &str) -> Option<String> {
    extract_xml_texts(body, local_name).into_iter().next()
}

fn extract_xml_texts(body: &str, local_name: &str) -> Vec<String> {
    let mut values = Vec::new();
    let mut remaining = body;

    while let Some(start_index) = remaining.find(&format!(":{local_name}")) {
        let Some(open_start) = remaining[..start_index].rfind('<') else {
            break;
        };
        let Some(open_end_offset) = remaining[start_index..].find('>') else {
            break;
        };
        let value_start = start_index + open_end_offset + 1;
        let Some(close_start_offset) = remaining[value_start..].find("</") else {
            break;
        };
        let value_end = value_start + close_start_offset;

        if remaining[open_start..start_index].contains('/') {
            remaining = &remaining[value_start..];
            continue;
        }

        values.push(remaining[value_start..value_end].trim().to_string());
        remaining = &remaining[value_end..];
    }

    values
}

fn error_response(status: StatusCode, condition_xml: &'static str) -> DavResponse {
    DavResponse::new(status).with_body(DavErrorTemplate { condition_xml }.render().unwrap())
}

fn mkcalendar_property_error_response(href: String, properties: Vec<&'static str>) -> DavResponse {
    DavResponse::new(StatusCode::MULTI_STATUS).with_body(
        MkcalendarPropertyErrorTemplate { href, properties }
            .render()
            .unwrap(),
    )
}
