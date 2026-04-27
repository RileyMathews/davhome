use askama::Template;
use axum::{
    http::{HeaderMap, HeaderValue, StatusCode, header},
    response::{IntoResponse, Response},
};

#[derive(Template)]
#[template(path = "dav/empty.xml", escape = "none")]
struct EmptyDavTemplate;

pub struct RenderedDavProperty {
    pub xml: String,
}

impl RenderedDavProperty {
    pub(super) fn new(template: impl Template) -> Self {
        Self {
            xml: template.render().unwrap(),
        }
    }
}

pub struct DavResponse {
    status: StatusCode,
    headers: HeaderMap,
    body: Option<String>,
}

impl DavResponse {
    pub fn new(status: StatusCode) -> Self {
        Self {
            status,
            headers: HeaderMap::new(),
            body: None,
        }
    }

    pub fn with_header(mut self, name: header::HeaderName, value: HeaderValue) -> Self {
        self.headers.insert(name, value);
        self
    }

    pub fn with_body(mut self, body: String) -> Self {
        self.body = Some(body);
        self
    }
}

impl IntoResponse for DavResponse {
    fn into_response(self) -> Response {
        let body = self
            .body
            .unwrap_or_else(|| EmptyDavTemplate.render().unwrap());
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

pub(super) fn propfind_response(template: impl Template) -> DavResponse {
    DavResponse::new(StatusCode::MULTI_STATUS).with_body(template.render().unwrap())
}
