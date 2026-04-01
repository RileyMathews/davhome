use std::{
    collections::HashSet,
    convert::Infallible,
    future::Future,
    pin::Pin,
    task::{Context, Poll},
};

use axum::{
    extract::Request,
    handler::Handler,
    http::{HeaderValue, Method, StatusCode, header},
    response::{IntoResponse, Response},
};
use tower::{Service, ServiceExt, util::BoxCloneSyncService};

type BoxedRoute = BoxCloneSyncService<Request, Response, Infallible>;
type BoxFuture = Pin<Box<dyn Future<Output = Result<Response, Infallible>> + Send>>;

#[derive(Clone)]
struct MethodRoute {
    method: Method,
    service: BoxedRoute,
}

#[derive(Clone)]
pub struct CustomMethodRouter<S> {
    state: S,
    routes: Vec<MethodRoute>,
    fallback: Option<BoxedRoute>,
    allow_header: Option<HeaderValue>,
}

impl<S> CustomMethodRouter<S>
where
    S: Clone + Send + Sync + 'static,
{
    pub fn new(state: S) -> Self {
        Self {
            state,
            routes: Vec::new(),
            fallback: None,
            allow_header: None,
        }
    }

    pub fn on<M, H, T>(mut self, method: M, handler: H) -> Self
    where
        M: Into<Method>,
        H: Handler<T, S>,
        T: 'static,
    {
        let method = method.into();

        if self.routes.iter().any(|route| route.method == method) {
            panic!("Overlapping custom method route for `{}`", method);
        }

        self.routes.push(MethodRoute {
            method,
            service: BoxCloneSyncService::new(handler.with_state(self.state.clone())),
        });
        self.allow_header = build_allow_header(&self.routes);
        self
    }

    pub fn fallback<H, T>(mut self, handler: H) -> Self
    where
        H: Handler<T, S>,
        T: 'static,
    {
        self.fallback = Some(BoxCloneSyncService::new(
            handler.with_state(self.state.clone()),
        ));
        self
    }
}

impl Default for CustomMethodRouter<()>
where
    (): Clone + Send + Sync + 'static,
{
    fn default() -> Self {
        Self::new(())
    }
}

impl<S> Service<Request> for CustomMethodRouter<S>
where
    S: Clone + Send + Sync + 'static,
{
    type Response = Response;
    type Error = Infallible;
    type Future = BoxFuture;

    fn poll_ready(&mut self, _cx: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        Poll::Ready(Ok(()))
    }

    fn call(&mut self, req: Request) -> Self::Future {
        if let Some(route) = self
            .routes
            .iter()
            .find(|route| route.method == *req.method())
        {
            let service = route.service.clone();
            return Box::pin(async move { service.oneshot(req).await });
        }

        if let Some(fallback) = &self.fallback {
            let service = fallback.clone();
            return Box::pin(async move { service.oneshot(req).await });
        }

        let allow_header = self.allow_header.clone();
        Box::pin(async move {
            let mut response = StatusCode::METHOD_NOT_ALLOWED.into_response();
            if let Some(allow_header) = allow_header {
                response.headers_mut().insert(header::ALLOW, allow_header);
            }
            Ok(response)
        })
    }
}

fn build_allow_header(routes: &[MethodRoute]) -> Option<HeaderValue> {
    let mut seen = HashSet::new();
    let mut methods = Vec::new();

    for route in routes {
        if seen.insert(route.method.clone()) {
            methods.push(route.method.as_str());
        }
    }

    if methods.is_empty() {
        return None;
    }

    HeaderValue::from_str(&methods.join(", ")).ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use axum::http::Request as HttpRequest;

    use crate::dav_method::DavMethod;

    async fn ok_handler() -> &'static str {
        "ok"
    }

    #[tokio::test]
    async fn custom_method_router_dispatches_custom_methods() {
        let mkcol = Method::from(DavMethod::Mkcol);
        let mut router = CustomMethodRouter::new(()).on(DavMethod::Mkcol, ok_handler);

        let response = router
            .call(
                HttpRequest::builder()
                    .method(mkcol)
                    .uri("/")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn custom_method_router_sets_allow_header_for_unmatched_methods() {
        let mut router = CustomMethodRouter::new(())
            .on(Method::DELETE, ok_handler)
            .on(Method::OPTIONS, ok_handler);

        let response = router
            .call(
                HttpRequest::builder()
                    .method(Method::GET)
                    .uri("/")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::METHOD_NOT_ALLOWED);
        assert_eq!(
            response.headers().get(header::ALLOW).unwrap(),
            "DELETE, OPTIONS"
        );
    }
}
