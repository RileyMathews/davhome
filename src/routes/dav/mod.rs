pub mod response;

mod calendar_collection;
mod calendar_home;
mod root;

pub use calendar_collection::{
    handle_collection_delete, handle_collection_mkcol, handle_collection_options,
    handle_collection_propfind,
};
pub use calendar_home::{handle_home_fallback, handle_home_options, handle_home_propfind};
pub use response::DavResponse;
pub use root::{handle_root_fallback, handle_root_options, handle_root_propfind};

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
    })
}

pub(super) fn principal_href(username: &str) -> String {
    format!("/dav/principals/{username}/")
}

pub(super) fn calendar_home_href(username: &str) -> String {
    format!("/dav/calendars/{username}/")
}

pub(super) fn calendar_collection_href(username: &str, binding: &str) -> String {
    format!("/dav/calendars/{username}/{binding}/")
}
