use std::{collections::HashSet, time::SystemTime};

use askama::Template;
use axum::{
    body::Bytes,
    extract::{Path, State},
    http::{HeaderMap, HeaderValue, StatusCode, header},
};
use sha2::{Digest, Sha256};
use sqlx::PgPool;

use crate::{
    auth,
    models::{calendar, user},
};

use super::{
    RequestedProps, calendar_object_href, parse_propfind_request, principal_uid_matches_user,
    response::{DavResponse, RenderedDavProperty, propfind_response},
};

const OBJECT_ALLPROP: RequestedProps = RequestedProps {
    current_user_principal: false,
    principal_url: false,
    calendar_home_set: false,
    resourcetype: true,
    displayname: false,
    supported_calendar_component_set: false,
    supported_report_set: false,
    sync_token: false,
    getetag: true,
    getlastmodified: true,
    getcontenttype: true,
    getcontentlength: true,
};

#[derive(Template)]
#[template(path = "dav/propfind_calendar_collection.xml")]
struct CalendarObjectPropfindTemplate {
    href: String,
    properties: Vec<RenderedDavProperty>,
}

#[derive(Template)]
#[template(source = "{{ content|safe }}", ext = "txt", escape = "none")]
struct CalendarObjectTemplate {
    content: String,
}

#[derive(Template)]
#[template(path = "dav/properties/resourcetype.xml")]
struct ResourcetypePropertyTemplate {
    collection: bool,
    calendar: bool,
}

#[derive(Template)]
#[template(path = "dav/properties/getetag.xml")]
struct GetetagPropertyTemplate {
    etag: String,
}

#[derive(Template)]
#[template(path = "dav/properties/getlastmodified.xml")]
struct GetlastmodifiedPropertyTemplate {
    last_modified: String,
}

#[derive(Template)]
#[template(path = "dav/properties/getcontenttype.xml")]
struct GetcontenttypePropertyTemplate {
    content_type: String,
}

#[derive(Template)]
#[template(path = "dav/properties/getcontentlength.xml")]
struct GetcontentlengthPropertyTemplate {
    content_length: usize,
}

#[derive(Template)]
#[template(path = "dav/error.xml")]
struct DavErrorTemplate {
    condition_xml: &'static str,
}

struct ParsedCalendarObject {
    uid: String,
    component_type: String,
}

pub async fn handle_object_options() -> DavResponse {
    DavResponse::new(StatusCode::NO_CONTENT)
}

pub async fn handle_object_mkcol(
    State(pool): State<PgPool>,
    headers: HeaderMap,
    Path((principal_uid, binding, _object)): Path<(String, String, String)>,
) -> DavResponse {
    match load_binding(&pool, &headers, &principal_uid, &binding).await {
        Ok(_) => error_response(
            StatusCode::FORBIDDEN,
            "<C:calendar-collection-location-ok/>",
        ),
        Err(response) => response,
    }
}

pub async fn handle_object_mkcalendar(
    State(pool): State<PgPool>,
    headers: HeaderMap,
    Path((principal_uid, binding, _object)): Path<(String, String, String)>,
) -> DavResponse {
    match load_binding(&pool, &headers, &principal_uid, &binding).await {
        Ok(_) => error_response(
            StatusCode::FORBIDDEN,
            "<C:calendar-collection-location-ok/>",
        ),
        Err(response) => response,
    }
}

pub async fn handle_object_put(
    State(pool): State<PgPool>,
    headers: HeaderMap,
    Path((principal_uid, binding, object)): Path<(String, String, String)>,
    body: Bytes,
) -> DavResponse {
    let (user, binding) = match load_binding(&pool, &headers, &principal_uid, &binding).await {
        Ok(context) => context,
        Err(response) => return response,
    };

    if object.trim().is_empty() || object.contains('/') {
        return DavResponse::new(StatusCode::BAD_REQUEST);
    }

    if !is_supported_calendar_content_type(&headers) {
        return DavResponse::new(StatusCode::UNSUPPORTED_MEDIA_TYPE);
    }

    let content = match String::from_utf8(body.to_vec()) {
        Ok(content) => content,
        Err(_) => return DavResponse::new(StatusCode::BAD_REQUEST),
    };
    let parsed = match parse_icalendar_object(&content) {
        Ok(parsed) => parsed,
        Err(_) => return DavResponse::new(StatusCode::BAD_REQUEST),
    };

    if !binding
        .supported_component_set
        .iter()
        .any(|component| component == &parsed.component_type)
    {
        return DavResponse::new(StatusCode::FORBIDDEN);
    }

    let existing =
        match calendar::find_dav_calendar_object(&pool, binding.calendar_id, &object).await {
            Ok(existing) => existing,
            Err(_) => return DavResponse::new(StatusCode::INTERNAL_SERVER_ERROR),
        };

    if put_precondition_failed(&headers, existing.as_ref()) {
        return DavResponse::new(StatusCode::PRECONDITION_FAILED);
    }

    let input = calendar::CalendarObjectInput {
        href: object,
        uid: parsed.uid,
        component_type: parsed.component_type,
        etag: calendar_object_etag(content.as_bytes()),
        icalendar: content,
    };

    match calendar::put_dav_calendar_object(&pool, binding.calendar_id, binding.id, user.id, input)
        .await
    {
        Ok(calendar::CalendarObjectWriteResult::Created(object)) => {
            object_empty_response(StatusCode::CREATED, &object)
        }
        Ok(calendar::CalendarObjectWriteResult::Updated(object))
        | Ok(calendar::CalendarObjectWriteResult::Unchanged(object)) => {
            object_empty_response(StatusCode::NO_CONTENT, &object)
        }
        Err(calendar::CalendarObjectWriteError::UidConflict { .. }) => {
            DavResponse::new(StatusCode::CONFLICT)
        }
        Err(calendar::CalendarObjectWriteError::Sqlx(_)) => {
            DavResponse::new(StatusCode::INTERNAL_SERVER_ERROR)
        }
    }
}

pub async fn handle_object_get(
    State(pool): State<PgPool>,
    headers: HeaderMap,
    Path((principal_uid, binding, object)): Path<(String, String, String)>,
) -> DavResponse {
    let (_, _, object) = match load_object(&pool, &headers, &principal_uid, &binding, &object).await
    {
        Ok(context) => context,
        Err(response) => return response,
    };

    object_body_response(StatusCode::OK, object)
}

pub async fn handle_object_head(
    State(pool): State<PgPool>,
    headers: HeaderMap,
    Path((principal_uid, binding, object)): Path<(String, String, String)>,
) -> DavResponse {
    let (_, _, object) = match load_object(&pool, &headers, &principal_uid, &binding, &object).await
    {
        Ok(context) => context,
        Err(response) => return response,
    };

    object_empty_response(StatusCode::OK, &object)
}

pub async fn handle_object_propfind(
    State(pool): State<PgPool>,
    headers: HeaderMap,
    Path((principal_uid, binding, object)): Path<(String, String, String)>,
    body: Bytes,
) -> DavResponse {
    let (user, binding, object) =
        match load_object(&pool, &headers, &principal_uid, &binding, &object).await {
            Ok(context) => context,
            Err(response) => return response,
        };

    let request = parse_propfind_request(&body);
    let props = request.requested_props(OBJECT_ALLPROP);

    propfind_response(CalendarObjectPropfindTemplate {
        href: calendar_object_href(&user, &binding.uri, &object.href),
        properties: object_properties(&props, &object),
    })
}

pub async fn handle_object_delete(
    State(pool): State<PgPool>,
    headers: HeaderMap,
    Path((principal_uid, binding, object)): Path<(String, String, String)>,
) -> DavResponse {
    let (user, binding, existing) =
        match load_object(&pool, &headers, &principal_uid, &binding, &object).await {
            Ok(context) => context,
            Err(response) => return response,
        };

    if delete_precondition_failed(&headers, &existing) {
        return DavResponse::new(StatusCode::PRECONDITION_FAILED);
    }

    match calendar::delete_dav_calendar_object(
        &pool,
        binding.calendar_id,
        binding.id,
        user.id,
        &object,
    )
    .await
    {
        Ok(Some(_)) => DavResponse::new(StatusCode::NO_CONTENT),
        Ok(None) => DavResponse::new(StatusCode::NOT_FOUND),
        Err(_) => DavResponse::new(StatusCode::INTERNAL_SERVER_ERROR),
    }
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

async fn load_object(
    pool: &PgPool,
    headers: &HeaderMap,
    principal_uid: &str,
    binding: &str,
    object: &str,
) -> Result<
    (
        user::User,
        calendar::DavCalendarBinding,
        calendar::DavCalendarObject,
    ),
    DavResponse,
> {
    let (user, binding) = load_binding(pool, headers, principal_uid, binding).await?;
    let object = match calendar::find_dav_calendar_object(pool, binding.calendar_id, object).await {
        Ok(Some(object)) => object,
        Ok(None) => return Err(DavResponse::new(StatusCode::NOT_FOUND)),
        Err(_) => return Err(DavResponse::new(StatusCode::INTERNAL_SERVER_ERROR)),
    };

    Ok((user, binding, object))
}

fn object_body_response(status: StatusCode, object: calendar::DavCalendarObject) -> DavResponse {
    let body = CalendarObjectTemplate {
        content: object.icalendar.clone(),
    }
    .render()
    .unwrap();

    object_headers(DavResponse::new(status), &object).with_body(body)
}

fn object_empty_response(status: StatusCode, object: &calendar::DavCalendarObject) -> DavResponse {
    object_headers(DavResponse::new(status), object)
}

fn object_headers(response: DavResponse, object: &calendar::DavCalendarObject) -> DavResponse {
    response
        .with_header(
            header::CONTENT_TYPE,
            HeaderValue::from_str(&object_content_type(&object.component_type)).unwrap(),
        )
        .with_header(header::ETAG, HeaderValue::from_str(&object.etag).unwrap())
        .with_header(
            header::LAST_MODIFIED,
            HeaderValue::from_str(&last_modified_http(object.last_modified_at)).unwrap(),
        )
}

fn object_properties(
    props: &RequestedProps,
    object: &calendar::DavCalendarObject,
) -> Vec<RenderedDavProperty> {
    let mut properties = Vec::new();

    if props.resourcetype {
        properties.push(RenderedDavProperty::new(ResourcetypePropertyTemplate {
            collection: false,
            calendar: false,
        }));
    }
    if props.getetag {
        properties.push(RenderedDavProperty::new(GetetagPropertyTemplate {
            etag: object.etag.clone(),
        }));
    }
    if props.getlastmodified {
        properties.push(RenderedDavProperty::new(GetlastmodifiedPropertyTemplate {
            last_modified: last_modified_http(object.last_modified_at),
        }));
    }
    if props.getcontenttype {
        properties.push(RenderedDavProperty::new(GetcontenttypePropertyTemplate {
            content_type: object_content_type(&object.component_type),
        }));
    }
    if props.getcontentlength {
        properties.push(RenderedDavProperty::new(GetcontentlengthPropertyTemplate {
            content_length: object.icalendar.len(),
        }));
    }

    properties
}

fn is_supported_calendar_content_type(headers: &HeaderMap) -> bool {
    headers
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .map(|content_type| {
            content_type
                .split(';')
                .next()
                .unwrap_or_default()
                .trim()
                .eq_ignore_ascii_case("text/calendar")
        })
        .unwrap_or(true)
}

fn put_precondition_failed(
    headers: &HeaderMap,
    existing: Option<&calendar::DavCalendarObject>,
) -> bool {
    if headers
        .get(header::IF_NONE_MATCH)
        .and_then(|value| value.to_str().ok())
        == Some("*")
        && existing.is_some()
    {
        return true;
    }

    let Some(if_match) = headers
        .get(header::IF_MATCH)
        .and_then(|value| value.to_str().ok())
    else {
        return false;
    };

    match existing {
        Some(object) => if_match != "*" && if_match != object.etag,
        None => true,
    }
}

fn delete_precondition_failed(headers: &HeaderMap, object: &calendar::DavCalendarObject) -> bool {
    headers
        .get(header::IF_MATCH)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|if_match| if_match != "*" && if_match != object.etag)
}

fn parse_icalendar_object(content: &str) -> Result<ParsedCalendarObject, ()> {
    let mut stack = Vec::new();
    let mut active_main_depth = None;
    let mut component_type = None;
    let mut uids = HashSet::new();
    let mut saw_vcalendar = false;

    for line in unfold_icalendar_lines(content) {
        if line.trim().is_empty() {
            continue;
        }

        let Some((name, value)) = line.split_once(':') else {
            return Err(());
        };
        let name = name
            .split(';')
            .next()
            .unwrap_or_default()
            .to_ascii_uppercase();
        let value = value.trim();
        let upper_value = value.to_ascii_uppercase();

        match name.as_str() {
            "BEGIN" => {
                stack.push(upper_value.clone());
                if upper_value == "VCALENDAR" {
                    if stack.len() != 1 || saw_vcalendar {
                        return Err(());
                    }
                    saw_vcalendar = true;
                } else if is_main_calendar_component(&upper_value) {
                    if active_main_depth.is_some()
                        || !stack.iter().any(|entry| entry == "VCALENDAR")
                    {
                        return Err(());
                    }
                    if let Some(existing_type) = &component_type {
                        if existing_type != &upper_value {
                            return Err(());
                        }
                    } else {
                        component_type = Some(upper_value.clone());
                    }
                    active_main_depth = Some(stack.len());
                }
            }
            "END" => {
                if stack.last() != Some(&upper_value) {
                    return Err(());
                }
                if active_main_depth == Some(stack.len()) {
                    active_main_depth = None;
                }
                stack.pop();
            }
            "METHOD" if stack.last().is_some_and(|entry| entry == "VCALENDAR") => {
                return Err(());
            }
            "UID" if active_main_depth == Some(stack.len()) => {
                if value.is_empty() {
                    return Err(());
                }
                uids.insert(value.to_string());
            }
            _ => {}
        }
    }

    if !saw_vcalendar || !stack.is_empty() || uids.len() != 1 {
        return Err(());
    }

    Ok(ParsedCalendarObject {
        uid: uids.into_iter().next().unwrap(),
        component_type: component_type.ok_or(())?,
    })
}

fn unfold_icalendar_lines(content: &str) -> Vec<String> {
    let normalized = content.replace("\r\n", "\n").replace('\r', "\n");
    let mut lines = Vec::<String>::new();

    for line in normalized.split('\n') {
        if line.starts_with(' ') || line.starts_with('\t') {
            if let Some(previous) = lines.last_mut() {
                previous.push_str(&line[1..]);
            }
        } else {
            lines.push(line.to_string());
        }
    }

    lines
}

fn is_main_calendar_component(component: &str) -> bool {
    matches!(component, "VEVENT" | "VTODO" | "VJOURNAL")
}

fn calendar_object_etag(content: &[u8]) -> String {
    let digest = Sha256::digest(content);
    format!("\"{digest:x}\"")
}

fn object_content_type(component_type: &str) -> String {
    format!("text/calendar;charset=utf-8;component={component_type}")
}

fn last_modified_http(last_modified_at: chrono::DateTime<chrono::Utc>) -> String {
    httpdate::fmt_http_date(SystemTime::from(last_modified_at))
}

fn error_response(status: StatusCode, condition_xml: &'static str) -> DavResponse {
    DavResponse::new(status).with_body(DavErrorTemplate { condition_xml }.render().unwrap())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_icalendar_object_extracts_event_uid() {
        let parsed = parse_icalendar_object(
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:event-1\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n",
        )
        .unwrap();

        assert_eq!(parsed.uid, "event-1");
        assert_eq!(parsed.component_type, "VEVENT");
    }

    #[test]
    fn parse_icalendar_object_rejects_mixed_components() {
        let parsed = parse_icalendar_object(
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:same\nEND:VEVENT\nBEGIN:VTODO\nUID:same\nEND:VTODO\nEND:VCALENDAR\n",
        );

        assert!(parsed.is_err());
    }

    #[test]
    fn parse_icalendar_object_rejects_multiple_uids() {
        let parsed = parse_icalendar_object(
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:first\nEND:VEVENT\nBEGIN:VEVENT\nUID:second\nEND:VEVENT\nEND:VCALENDAR\n",
        );

        assert!(parsed.is_err());
    }
}
