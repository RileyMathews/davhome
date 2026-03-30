#![allow(dead_code)]

use axum::{Router, body::Body, http::Request, response::Response};
use davhome::{auth, build_app, models::user};
use http_body_util::BodyExt;
use sqlx::PgPool;
use tower::util::ServiceExt;

pub async fn create_user_with_password(
    pool: &PgPool,
    username: &str,
    password: &str,
) -> sqlx::Result<user::User> {
    let password_hash = auth::hash_password(password).unwrap();
    user::create_user(pool, username, &password_hash).await
}

pub fn app(pool: PgPool) -> Router {
    build_app(pool)
}

pub async fn send(app: Router, request: Request<Body>) -> Response {
    app.oneshot(request).await.unwrap()
}

pub async fn body_text(response: Response) -> String {
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    String::from_utf8(bytes.to_vec()).unwrap()
}
