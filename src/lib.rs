use askama::Template;
use axum::{
    Router,
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
        .layer(CookieManagerLayer::new())
        .layer(
            TraceLayer::new_for_http()
                .make_span_with(trace::DefaultMakeSpan::new().level(Level::INFO))
                .on_response(trace::DefaultOnResponse::new().level(Level::INFO)),
        )
        .with_state(pool)
}
