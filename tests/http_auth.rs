use axum::{
    body::Body,
    http::{Request, StatusCode, header},
};
use sqlx::PgPool;

mod common;

#[tokio::test]
async fn http_get_signup_returns_signup_form() {
    let app =
        common::app(PgPool::connect_lazy("postgres://unused:unused@localhost/unused").unwrap());

    let response = common::send(
        app,
        Request::builder()
            .uri("/signup")
            .body(Body::empty())
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::OK);
    let body = common::body_text(response).await;
    assert!(body.contains("<h1>Sign Up</h1>"));
}

#[tokio::test]
async fn http_get_signin_returns_signin_form() {
    let app =
        common::app(PgPool::connect_lazy("postgres://unused:unused@localhost/unused").unwrap());

    let response = common::send(
        app,
        Request::builder()
            .uri("/signin")
            .body(Body::empty())
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::OK);
    let body = common::body_text(response).await;
    assert!(body.contains("<h1>Sign In</h1>"));
}

#[tokio::test]
async fn http_get_root_signed_out_returns_welcome_page() {
    let app =
        common::app(PgPool::connect_lazy("postgres://unused:unused@localhost/unused").unwrap());

    let response = common::send(
        app,
        Request::builder().uri("/").body(Body::empty()).unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::OK);
    let body = common::body_text(response).await;
    assert!(body.contains("DavHome"));
}

#[sqlx::test]
async fn http_signup_with_missing_fields_shows_validation_errors(pool: PgPool) -> sqlx::Result<()> {
    let app = common::app(pool);

    let response = common::send(
        app,
        Request::builder()
            .method("POST")
            .uri("/signup")
            .header(header::CONTENT_TYPE, "application/x-www-form-urlencoded")
            .body(Body::from("username=&password=&confirm_password="))
            .unwrap(),
    )
    .await;

    let body = common::body_text(response).await;
    assert!(body.contains("username is required"));
    assert!(body.contains("password is required"));
    assert!(body.contains("confirm_password is required"));
    Ok(())
}

#[sqlx::test]
async fn http_signup_with_mismatched_passwords_shows_error(pool: PgPool) -> sqlx::Result<()> {
    let app = common::app(pool);

    let response = common::send(
        app,
        Request::builder()
            .method("POST")
            .uri("/signup")
            .header(header::CONTENT_TYPE, "application/x-www-form-urlencoded")
            .body(Body::from(
                "username=alice&password=1234567890123456&confirm_password=6543210987654321",
            ))
            .unwrap(),
    )
    .await;

    let body = common::body_text(response).await;
    assert!(body.contains("Passwords do not match"));
    Ok(())
}

#[sqlx::test]
async fn http_signup_success_sets_session_cookie_and_redirects(pool: PgPool) -> sqlx::Result<()> {
    let app = common::app(pool.clone());

    let response = common::send(
        app,
        Request::builder()
            .method("POST")
            .uri("/signup")
            .header(header::CONTENT_TYPE, "application/x-www-form-urlencoded")
            .body(Body::from(
                "username=alice&password=1234567890123456&confirm_password=1234567890123456",
            ))
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::OK);
    assert!(response.headers().get(header::SET_COOKIE).is_some());
    let body = common::body_text(response).await;
    assert!(body.contains("url=/"));

    let count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM users WHERE username = $1")
        .bind("alice")
        .fetch_one(&pool)
        .await?;
    assert_eq!(count, 1);
    Ok(())
}

#[sqlx::test]
async fn http_signin_with_wrong_password_shows_error(pool: PgPool) -> sqlx::Result<()> {
    common::create_user_with_password(&pool, "alice", "1234567890123456").await?;
    let app = common::app(pool);

    let response = common::send(
        app,
        Request::builder()
            .method("POST")
            .uri("/signin")
            .header(header::CONTENT_TYPE, "application/x-www-form-urlencoded")
            .body(Body::from("username=alice&password=wrongpassword000"))
            .unwrap(),
    )
    .await;

    let body = common::body_text(response).await;
    assert!(body.contains("Invalid username or password"));
    Ok(())
}

#[sqlx::test]
async fn http_signin_success_sets_session_cookie_and_redirects(pool: PgPool) -> sqlx::Result<()> {
    common::create_user_with_password(&pool, "alice", "1234567890123456").await?;
    let app = common::app(pool);

    let response = common::send(
        app,
        Request::builder()
            .method("POST")
            .uri("/signin")
            .header(header::CONTENT_TYPE, "application/x-www-form-urlencoded")
            .body(Body::from("username=alice&password=1234567890123456"))
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::OK);
    assert!(response.headers().get(header::SET_COOKIE).is_some());
    let body = common::body_text(response).await;
    assert!(body.contains("url=/"));
    Ok(())
}
