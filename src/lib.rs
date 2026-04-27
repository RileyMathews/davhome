use askama::Template;
use axum::{
    Router,
    http::Method,
    response::Html,
    routing::{get, post},
};
use sqlx::PgPool;
use tower_cookies::{CookieManagerLayer, Cookies};
use tower_http::trace::{self, TraceLayer};
use tracing::Level;

use crate::auth::get_user_id_from_session;
use crate::models::user;

pub mod auth;
pub mod custom_method_router;
pub mod dav_method;
pub mod db;
pub mod forms;
pub mod models;
pub mod routes;

#[derive(Template)]
#[template(path = "index.html")]
struct IndexTemplate {
    username: Option<String>,
}

async fn index(pool: PgPool, cookies: Cookies) -> Html<String> {
    let user = if let Some(user_id) = get_user_id_from_session(&cookies) {
        user::find_by_id(&pool, user_id).await.ok().flatten()
    } else {
        None
    };

    match user {
        Some(_) => routes::calendars::calendars_page(axum::extract::State(pool), cookies).await,
        None => {
            let template = IndexTemplate { username: None };
            Html(template.render().unwrap())
        }
    }
}

pub fn build_app(pool: PgPool) -> Router {
    Router::new()
        .route(
            "/",
            get({
                let pool = pool.clone();
                move |cookies: Cookies| {
                    let pool = pool.clone();
                    async move { index(pool, cookies).await }
                }
            }),
        )
        .route(
            "/signup",
            get(routes::auth::signup_page).post(routes::auth::handle_signup),
        )
        .route(
            "/signin",
            get(routes::auth::signin_page).post(routes::auth::handle_signin),
        )
        .route("/signout", post(routes::auth::handle_signout))
        .route(
            "/calendars",
            post(routes::calendars::handle_create_calendar),
        )
        .route(
            "/calendars/delete",
            post(routes::calendars::handle_delete_calendar),
        )
        .route(
            "/.well-known/caldav",
            get(routes::dav::handle_well_known_caldav),
        )
        .route(
            "/.well-known/caldav/",
            get(routes::dav::handle_well_known_caldav),
        )
        .route_service(
            "/dav",
            crate::custom_method_router::CustomMethodRouter::new(pool.clone())
                .on(
                    crate::dav_method::DavMethod::Options,
                    routes::dav::handle_root_options,
                )
                .on(
                    crate::dav_method::DavMethod::Propfind,
                    routes::dav::handle_root_propfind,
                )
                .fallback(routes::dav::handle_root_fallback),
        )
        .route_service(
            "/dav/",
            crate::custom_method_router::CustomMethodRouter::new(pool.clone())
                .on(
                    crate::dav_method::DavMethod::Options,
                    routes::dav::handle_root_options,
                )
                .on(
                    crate::dav_method::DavMethod::Propfind,
                    routes::dav::handle_root_propfind,
                )
                .fallback(routes::dav::handle_root_fallback),
        )
        .route_service(
            "/dav/principals/users",
            crate::custom_method_router::CustomMethodRouter::new(pool.clone())
                .on(
                    crate::dav_method::DavMethod::Options,
                    routes::dav::handle_principal_collection_options,
                )
                .on(
                    crate::dav_method::DavMethod::Propfind,
                    routes::dav::handle_principal_collection_propfind,
                ),
        )
        .route_service(
            "/dav/principals/users/",
            crate::custom_method_router::CustomMethodRouter::new(pool.clone())
                .on(
                    crate::dav_method::DavMethod::Options,
                    routes::dav::handle_principal_collection_options,
                )
                .on(
                    crate::dav_method::DavMethod::Propfind,
                    routes::dav::handle_principal_collection_propfind,
                ),
        )
        .route_service(
            "/dav/principals/__uids__/{principal_uid}",
            crate::custom_method_router::CustomMethodRouter::new(pool.clone())
                .on(
                    crate::dav_method::DavMethod::Options,
                    routes::dav::handle_principal_options,
                )
                .on(
                    crate::dav_method::DavMethod::Propfind,
                    routes::dav::handle_principal_propfind,
                ),
        )
        .route_service(
            "/dav/principals/__uids__/{principal_uid}/",
            crate::custom_method_router::CustomMethodRouter::new(pool.clone())
                .on(
                    crate::dav_method::DavMethod::Options,
                    routes::dav::handle_principal_options,
                )
                .on(
                    crate::dav_method::DavMethod::Propfind,
                    routes::dav::handle_principal_propfind,
                ),
        )
        .route_service(
            "/dav/calendars/__uids__/{principal_uid}",
            crate::custom_method_router::CustomMethodRouter::new(pool.clone())
                .on(
                    crate::dav_method::DavMethod::Options,
                    routes::dav::handle_home_options,
                )
                .on(
                    crate::dav_method::DavMethod::Propfind,
                    routes::dav::handle_home_propfind,
                )
                .fallback(routes::dav::handle_home_fallback),
        )
        .route_service(
            "/dav/calendars/__uids__/{principal_uid}/",
            crate::custom_method_router::CustomMethodRouter::new(pool.clone())
                .on(
                    crate::dav_method::DavMethod::Options,
                    routes::dav::handle_home_options,
                )
                .on(
                    crate::dav_method::DavMethod::Propfind,
                    routes::dav::handle_home_propfind,
                )
                .fallback(routes::dav::handle_home_fallback),
        )
        .route_service(
            "/dav/calendars/__uids__/{principal_uid}/{binding}",
            crate::custom_method_router::CustomMethodRouter::new(pool.clone())
                .on(
                    crate::dav_method::DavMethod::Options,
                    routes::dav::handle_collection_options,
                )
                .on(
                    crate::dav_method::DavMethod::Propfind,
                    routes::dav::handle_collection_propfind,
                )
                .on(
                    crate::dav_method::DavMethod::Delete,
                    routes::dav::handle_collection_delete,
                )
                .on(
                    crate::dav_method::DavMethod::Mkcol,
                    routes::dav::handle_collection_mkcol,
                )
                .on(
                    crate::dav_method::DavMethod::Mkcalendar,
                    routes::dav::handle_collection_mkcalendar,
                )
                .on(
                    crate::dav_method::DavMethod::Report,
                    routes::dav::handle_collection_report,
                ),
        )
        .route_service(
            "/dav/calendars/__uids__/{principal_uid}/{binding}/",
            crate::custom_method_router::CustomMethodRouter::new(pool.clone())
                .on(
                    crate::dav_method::DavMethod::Options,
                    routes::dav::handle_collection_options,
                )
                .on(
                    crate::dav_method::DavMethod::Propfind,
                    routes::dav::handle_collection_propfind,
                )
                .on(
                    crate::dav_method::DavMethod::Delete,
                    routes::dav::handle_collection_delete,
                )
                .on(
                    crate::dav_method::DavMethod::Mkcol,
                    routes::dav::handle_collection_mkcol,
                )
                .on(
                    crate::dav_method::DavMethod::Mkcalendar,
                    routes::dav::handle_collection_mkcalendar,
                )
                .on(
                    crate::dav_method::DavMethod::Report,
                    routes::dav::handle_collection_report,
                ),
        )
        .route_service(
            "/dav/calendars/__uids__/{principal_uid}/{binding}/{object}",
            crate::custom_method_router::CustomMethodRouter::new(pool.clone())
                .on(Method::OPTIONS, routes::dav::handle_object_options)
                .on(Method::GET, routes::dav::handle_object_get)
                .on(Method::HEAD, routes::dav::handle_object_head)
                .on(Method::PUT, routes::dav::handle_object_put)
                .on(
                    crate::dav_method::DavMethod::Mkcol,
                    routes::dav::handle_object_mkcol,
                )
                .on(
                    crate::dav_method::DavMethod::Mkcalendar,
                    routes::dav::handle_object_mkcalendar,
                )
                .on(
                    crate::dav_method::DavMethod::Propfind,
                    routes::dav::handle_object_propfind,
                )
                .on(Method::DELETE, routes::dav::handle_object_delete),
        )
        .layer(CookieManagerLayer::new())
        .layer(
            TraceLayer::new_for_http()
                .make_span_with(trace::DefaultMakeSpan::new().level(Level::INFO))
                .on_response(trace::DefaultOnResponse::new().level(Level::INFO)),
        )
        .with_state(pool)
}
