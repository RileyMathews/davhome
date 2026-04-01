use axum::http::Method;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum DavMethod {
    Delete,
    Mkcol,
    Options,
    Propfind,
    Proppatch,
    Report,
}

impl From<DavMethod> for Method {
    fn from(value: DavMethod) -> Method {
        match value {
            DavMethod::Delete => Method::DELETE,
            DavMethod::Mkcol => Method::from_bytes(b"MKCOL").unwrap(),
            DavMethod::Options => Method::OPTIONS,
            DavMethod::Propfind => Method::from_bytes(b"PROPFIND").unwrap(),
            DavMethod::Proppatch => Method::from_bytes(b"PROPPATCH").unwrap(),
            DavMethod::Report => Method::from_bytes(b"REPORT").unwrap(),
        }
    }
}
