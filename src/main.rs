use askama::Template;
use axum::{
    Router,
    response::Html,
    routing::{get, post},
};
use sqlx::PgPool;
use std::net::SocketAddr;
use tower_cookies::CookieManagerLayer;
use tower_http::trace::{self, TraceLayer};
use tracing::Level;
use tracing_subscriber::fmt::format::FmtSpan;

use crate::auth::get_user_id_from_session;
use crate::models::user;

mod auth;
mod db;
mod forms;
mod models;
mod routes;

#[derive(Template)]
#[template(path = "index.html")]
struct IndexTemplate {
    username: Option<String>,
}

async fn index(pool: PgPool, cookies: tower_cookies::Cookies) -> Html<String> {
    let user = if let Some(user_id) = get_user_id_from_session(&cookies) {
        user::find_by_id(&pool, user_id)
            .await
            .ok()
            .flatten()
    } else {
        None
    };

    match user {
        // If signed in, show calendar management page
        Some(_u) => routes::calendars::calendars_page(
            axum::extract::State(pool),
            cookies,
        ).await,
        // If not signed in, show welcome page
        None => {
            let template = IndexTemplate { username: None };
            Html(template.render().unwrap())
        }
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    dotenvy::dotenv().ok();

    tracing_subscriber::fmt()
        .json()
        .with_span_events(FmtSpan::CLOSE)
        .init();

    let database_url = std::env::var("DATABASE_URL")
        .unwrap_or_else(|_| "postgres://davhome:davhome@localhost:5432/davhome".to_string());

    let pool = sqlx::postgres::PgPoolOptions::new()
        .max_connections(5)
        .connect(&database_url)
        .await?;

    db::run_migrations(&pool).await?;
    tracing::info!("Database migrations completed successfully");

    let app = Router::new()
        .route(
            "/",
            get({
                let pool = pool.clone();
                move |cookies: tower_cookies::Cookies| {
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
        // Calendar management routes
        .route("/calendars", post(routes::calendars::handle_create_calendar))
        .route("/calendars/delete", post(routes::calendars::handle_delete_calendar))
        .layer(CookieManagerLayer::new())
        .layer(
            TraceLayer::new_for_http()
                .make_span_with(trace::DefaultMakeSpan::new().level(Level::INFO))
                .on_response(trace::DefaultOnResponse::new().level(Level::INFO)),
        )
        .with_state(pool);

    let addr = SocketAddr::from(([0, 0, 0, 0], 3000));
    let listener = tokio::net::TcpListener::bind(addr).await?;
    tracing::info!("Server running on http://{}", addr);

    axum::serve(listener, app).await?;

    Ok(())
}
