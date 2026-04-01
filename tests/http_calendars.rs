use axum::{
    body::Body,
    http::{Request, StatusCode, header},
};
use sqlx::PgPool;

mod common;

#[sqlx::test]
async fn http_get_root_signed_in_returns_calendar_page(pool: PgPool) -> sqlx::Result<()> {
    let user = common::create_user_with_password(&pool, "alice", "1234567890123456").await?;
    let app = common::app(pool);

    let response = common::send(
        app,
        Request::builder()
            .uri("/")
            .header(header::COOKIE, format!("session={}", user.id))
            .body(Body::empty())
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::OK);
    let body = common::body_text(response).await;
    assert!(body.contains("My Calendars"));
    assert!(body.contains("Create New Calendar"));
    Ok(())
}

#[sqlx::test]
async fn http_post_calendars_signed_out_redirects_to_signin(pool: PgPool) -> sqlx::Result<()> {
    let app = common::app(pool);

    let response = common::send(
        app,
        Request::builder()
            .method("POST")
            .uri("/calendars")
            .header(header::CONTENT_TYPE, "application/x-www-form-urlencoded")
            .body(Body::from("displayname=Personal"))
            .unwrap(),
    )
    .await;

    let body = common::body_text(response).await;
    assert!(body.contains("url=/signin"));
    Ok(())
}

#[sqlx::test]
async fn http_post_calendars_creates_calendar_and_redirects(pool: PgPool) -> sqlx::Result<()> {
    let user = common::create_user_with_password(&pool, "alice", "1234567890123456").await?;
    let app = common::app(pool.clone());

    let response = common::send(
        app,
        Request::builder()
            .method("POST")
            .uri("/calendars")
            .header(header::COOKIE, format!("session={}", user.id))
            .header(header::CONTENT_TYPE, "application/x-www-form-urlencoded")
            .body(Body::from(
                "displayname=Personal&description=Main%20calendar",
            ))
            .unwrap(),
    )
    .await;

    let body = common::body_text(response).await;
    assert!(body.contains("url=/"));

    let count: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM calendar_bindings WHERE principal_user_id = $1 AND displayname = $2",
    )
    .bind(user.id)
    .bind("Personal")
    .fetch_one(&pool)
    .await?;
    assert_eq!(count, 1);
    Ok(())
}

#[sqlx::test]
async fn http_post_calendars_blank_displayname_renders_validation_error(
    pool: PgPool,
) -> sqlx::Result<()> {
    let user = common::create_user_with_password(&pool, "alice", "1234567890123456").await?;
    let app = common::app(pool);

    let response = common::send(
        app,
        Request::builder()
            .method("POST")
            .uri("/calendars")
            .header(header::COOKIE, format!("session={}", user.id))
            .header(header::CONTENT_TYPE, "application/x-www-form-urlencoded")
            .body(Body::from("displayname=   &description=ignored"))
            .unwrap(),
    )
    .await;

    let body = common::body_text(response).await;
    assert!(body.contains("displayname is required"));
    Ok(())
}

#[sqlx::test]
async fn http_post_calendars_delete_removes_owned_calendar(pool: PgPool) -> sqlx::Result<()> {
    let user = common::create_user_with_password(&pool, "alice", "1234567890123456").await?;
    let calendar_id = davhome::models::calendar::create_calendar(
        &pool,
        user.id,
        &uuid::Uuid::new_v4().to_string(),
        "Personal",
        None,
    )
    .await?;
    let app = common::app(pool.clone());

    let response = common::send(
        app,
        Request::builder()
            .method("POST")
            .uri("/calendars/delete")
            .header(header::COOKIE, format!("session={}", user.id))
            .header(header::CONTENT_TYPE, "application/x-www-form-urlencoded")
            .body(Body::from(format!("calendar_id={calendar_id}")))
            .unwrap(),
    )
    .await;

    let body = common::body_text(response).await;
    assert!(body.contains("url=/"));

    let remaining: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM calendars WHERE id = $1")
        .bind(calendar_id)
        .fetch_one(&pool)
        .await?;
    assert_eq!(remaining, 0);
    Ok(())
}

#[sqlx::test]
async fn http_post_calendars_delete_invalid_uuid_redirects_safely(
    pool: PgPool,
) -> sqlx::Result<()> {
    let user = common::create_user_with_password(&pool, "alice", "1234567890123456").await?;
    let app = common::app(pool);

    let response = common::send(
        app,
        Request::builder()
            .method("POST")
            .uri("/calendars/delete")
            .header(header::COOKIE, format!("session={}", user.id))
            .header(header::CONTENT_TYPE, "application/x-www-form-urlencoded")
            .body(Body::from("calendar_id=not-a-uuid"))
            .unwrap(),
    )
    .await;

    let body = common::body_text(response).await;
    assert!(body.contains("url=/"));
    Ok(())
}

#[sqlx::test]
async fn http_post_calendars_delete_non_owner_does_not_remove_calendar(
    pool: PgPool,
) -> sqlx::Result<()> {
    let owner = common::create_user_with_password(&pool, "owner", "1234567890123456").await?;
    let other = common::create_user_with_password(&pool, "other", "1234567890123456").await?;
    let calendar_id = davhome::models::calendar::create_calendar(
        &pool,
        owner.id,
        &uuid::Uuid::new_v4().to_string(),
        "Shared",
        None,
    )
    .await?;
    let app = common::app(pool.clone());

    let response = common::send(
        app,
        Request::builder()
            .method("POST")
            .uri("/calendars/delete")
            .header(header::COOKIE, format!("session={}", other.id))
            .header(header::CONTENT_TYPE, "application/x-www-form-urlencoded")
            .body(Body::from(format!("calendar_id={calendar_id}")))
            .unwrap(),
    )
    .await;

    let body = common::body_text(response).await;
    assert!(body.contains("url=/"));

    let remaining: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM calendars WHERE id = $1")
        .bind(calendar_id)
        .fetch_one(&pool)
        .await?;
    assert_eq!(remaining, 1);
    Ok(())
}
