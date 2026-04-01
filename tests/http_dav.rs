use axum::{
    body::Body,
    http::{Request, StatusCode, header},
};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use sqlx::PgPool;

mod common;

fn basic_auth(username: &str, password: &str) -> String {
    let encoded = STANDARD.encode(format!("{username}:{password}"));
    format!("Basic {encoded}")
}

#[sqlx::test]
async fn http_mkcol_requires_basic_auth(pool: PgPool) -> sqlx::Result<()> {
    let app = common::app(pool);

    let response = common::send(
        app,
        Request::builder()
            .method("MKCOL")
            .uri("/dav/calendars/user01/litmus/")
            .body(Body::empty())
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert_eq!(
        response.headers().get(header::WWW_AUTHENTICATE).unwrap(),
        "Basic realm=\"davhome\""
    );
    Ok(())
}

#[sqlx::test]
async fn http_mkcol_creates_calendar_binding_for_authenticated_user(
    pool: PgPool,
) -> sqlx::Result<()> {
    let user = common::create_user_with_password(&pool, "user01", "user01").await?;
    let app = common::app(pool.clone());

    let response = common::send(
        app,
        Request::builder()
            .method("MKCOL")
            .uri("/dav/calendars/user01/litmus/")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .body(Body::empty())
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::CREATED);

    let count: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM calendar_bindings WHERE principal_user_id = $1 AND uri = $2",
    )
    .bind(user.id)
    .bind("litmus")
    .fetch_one(&pool)
    .await?;
    assert_eq!(count, 1);
    Ok(())
}

#[sqlx::test]
async fn http_mkcol_duplicate_collection_returns_method_not_allowed(
    pool: PgPool,
) -> sqlx::Result<()> {
    let user = common::create_user_with_password(&pool, "user01", "user01").await?;
    davhome::models::calendar::create_calendar(&pool, user.id, "litmus", "litmus", None).await?;
    let app = common::app(pool);

    let response = common::send(
        app,
        Request::builder()
            .method("MKCOL")
            .uri("/dav/calendars/user01/litmus/")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .body(Body::empty())
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::METHOD_NOT_ALLOWED);
    Ok(())
}

#[sqlx::test]
async fn http_delete_removes_owned_dav_collection(pool: PgPool) -> sqlx::Result<()> {
    let user = common::create_user_with_password(&pool, "user01", "user01").await?;
    davhome::models::calendar::create_calendar(&pool, user.id, "litmus", "litmus", None).await?;
    let app = common::app(pool.clone());

    let response = common::send(
        app,
        Request::builder()
            .method("DELETE")
            .uri("/dav/calendars/user01/litmus/")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .body(Body::empty())
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::NO_CONTENT);

    let remaining: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM calendar_bindings WHERE principal_user_id = $1 AND uri = $2",
    )
    .bind(user.id)
    .bind("litmus")
    .fetch_one(&pool)
    .await?;
    assert_eq!(remaining, 0);
    Ok(())
}

#[sqlx::test]
async fn http_delete_missing_dav_collection_returns_not_found(pool: PgPool) -> sqlx::Result<()> {
    common::create_user_with_password(&pool, "user01", "user01").await?;
    let app = common::app(pool);

    let response = common::send(
        app,
        Request::builder()
            .method("DELETE")
            .uri("/dav/calendars/user01/litmus/")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .body(Body::empty())
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    Ok(())
}
