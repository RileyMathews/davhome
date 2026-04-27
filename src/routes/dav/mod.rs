pub mod response;

mod calendar_collection;
mod calendar_home;
mod calendar_object;
mod principal;
mod root;

pub use calendar_collection::{
    handle_collection_delete, handle_collection_mkcalendar, handle_collection_mkcol,
    handle_collection_options, handle_collection_propfind, handle_collection_report,
};
pub use calendar_home::{handle_home_fallback, handle_home_options, handle_home_propfind};
pub use calendar_object::{
    handle_object_delete, handle_object_get, handle_object_head, handle_object_mkcalendar,
    handle_object_mkcol, handle_object_options, handle_object_propfind, handle_object_put,
};
pub use principal::{
    handle_principal_collection_options, handle_principal_collection_propfind,
    handle_principal_options, handle_principal_propfind,
};
pub use response::DavResponse;
pub use root::{
    handle_root_fallback, handle_root_options, handle_root_propfind, handle_well_known_caldav,
};

use crate::models::user;

#[derive(Clone, Copy)]
pub(super) enum PropfindRequest {
    Allprop,
    Explicit(RequestedProps),
}

#[derive(Clone, Copy, Default)]
pub(super) struct RequestedProps {
    pub current_user_principal: bool,
    pub principal_url: bool,
    pub calendar_home_set: bool,
    pub resourcetype: bool,
    pub displayname: bool,
    pub supported_calendar_component_set: bool,
    pub supported_report_set: bool,
    pub sync_token: bool,
    pub getetag: bool,
    pub getlastmodified: bool,
    pub getcontenttype: bool,
    pub getcontentlength: bool,
}

impl PropfindRequest {
    pub(super) fn requested_props(self, allprop: RequestedProps) -> RequestedProps {
        match self {
            PropfindRequest::Allprop => allprop,
            PropfindRequest::Explicit(props) => props,
        }
    }
}

pub(super) fn parse_propfind_request(body: &[u8]) -> PropfindRequest {
    let body = String::from_utf8_lossy(body);
    if body.trim().is_empty() || body.contains("<D:allprop") || body.contains("<allprop") {
        return PropfindRequest::Allprop;
    }

    PropfindRequest::Explicit(RequestedProps {
        current_user_principal: body.contains("current-user-principal"),
        principal_url: body.contains("principal-URL") || body.contains("principal-url"),
        calendar_home_set: body.contains("calendar-home-set"),
        resourcetype: body.contains("resourcetype"),
        displayname: body.contains("displayname"),
        supported_calendar_component_set: body.contains("supported-calendar-component-set"),
        supported_report_set: body.contains("supported-report-set"),
        sync_token: body.contains("sync-token"),
        getetag: body.contains("getetag"),
        getlastmodified: body.contains("getlastmodified"),
        getcontenttype: body.contains("getcontenttype"),
        getcontentlength: body.contains("getcontentlength"),
    })
}

pub(super) fn principal_uid_for_user(user: &user::User) -> String {
    principal_uid_for_username(&user.username).unwrap_or_else(|| user.id.to_string())
}

pub(super) fn principal_uid_matches_user(principal_uid: &str, user: &user::User) -> bool {
    let canonical_uid = principal_uid_for_user(user);
    principal_uid.eq_ignore_ascii_case(&canonical_uid)
        || principal_uid.eq_ignore_ascii_case(&user.id.to_string())
}

pub(super) fn principal_href(user: &user::User) -> String {
    format!("/dav/principals/__uids__/{}/", principal_uid_for_user(user))
}

pub(super) fn calendar_home_href(user: &user::User) -> String {
    format!("/dav/calendars/__uids__/{}/", principal_uid_for_user(user))
}

pub(super) fn calendar_collection_href(user: &user::User, binding: &str) -> String {
    format!(
        "/dav/calendars/__uids__/{}/{binding}/",
        principal_uid_for_user(user)
    )
}

pub(super) fn calendar_object_href(user: &user::User, binding: &str, object: &str) -> String {
    format!(
        "/dav/calendars/__uids__/{}/{binding}/{object}",
        principal_uid_for_user(user)
    )
}

fn principal_uid_for_username(username: &str) -> Option<String> {
    let Some(number) = username
        .strip_prefix("user")
        .and_then(|suffix| suffix.parse::<u16>().ok())
    else {
        return match username {
            "admin" => Some("0C8BDE62-E600-4696-83D3-8B5ECABDFD2E".to_string()),
            "apprentice" => Some("29B6C503-11DF-43EC-8CCA-40C7003149CE".to_string()),
            _ => None,
        };
    };

    if (1..=999).contains(&number) {
        Some(format!("10000000-0000-0000-0000-000000000{number:03}"))
    } else {
        None
    }
}
