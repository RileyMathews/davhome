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

fn propfind_body(props: &[&str]) -> Body {
    let props = props
        .iter()
        .map(|prop| format!("<D:{prop}/>"))
        .collect::<String>();

    Body::from(format!(
        r#"<?xml version="1.0" encoding="utf-8"?><D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"><D:prop>{props}</D:prop></D:propfind>"#
    ))
}

fn calendar_propfind_body(props: &[&str]) -> Body {
    let props = props
        .iter()
        .map(|prop| format!("<C:{prop}/>"))
        .collect::<String>();

    Body::from(format!(
        r#"<?xml version="1.0" encoding="utf-8"?><D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"><D:prop>{props}</D:prop></D:propfind>"#
    ))
}

#[sqlx::test]
async fn http_propfind_requires_basic_auth(pool: PgPool) -> sqlx::Result<()> {
    let app = common::app(pool);

    let response = common::send(
        app,
        Request::builder()
            .method("PROPFIND")
            .uri("/dav/")
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
async fn http_propfind_root_returns_current_user_principal(pool: PgPool) -> sqlx::Result<()> {
    common::create_user_with_password(&pool, "user01", "user01").await?;
    let app = common::app(pool);

    let response = common::send(
        app,
        Request::builder()
            .method("PROPFIND")
            .uri("/dav/")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .body(propfind_body(&["current-user-principal"]))
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::MULTI_STATUS);
    let body = common::body_text(response).await;
    assert!(body.contains("<D:href>/dav/</D:href>"));
    assert!(body.contains("<D:current-user-principal><D:href>/dav/principals/user01/</D:href></D:current-user-principal>"));
    Ok(())
}

#[sqlx::test]
async fn http_propfind_calendar_home_returns_calendar_home_set(pool: PgPool) -> sqlx::Result<()> {
    common::create_user_with_password(&pool, "user01", "user01").await?;
    let app = common::app(pool);

    let response = common::send(
        app,
        Request::builder()
            .method("PROPFIND")
            .uri("/dav/calendars/user01/")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .body(calendar_propfind_body(&["calendar-home-set"]))
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::MULTI_STATUS);
    let body = common::body_text(response).await;
    assert!(body.contains("<D:href>/dav/calendars/user01/</D:href>"));
    assert!(body.contains(
        "<C:calendar-home-set><D:href>/dav/calendars/user01/</D:href></C:calendar-home-set>"
    ));
    Ok(())
}

#[sqlx::test]
async fn http_propfind_depth_one_calendar_home_lists_bindings(pool: PgPool) -> sqlx::Result<()> {
    let user = common::create_user_with_password(&pool, "user01", "user01").await?;
    davhome::models::calendar::create_calendar(&pool, user.id, "personal", "Personal", None)
        .await?;
    let app = common::app(pool);

    let response = common::send(
        app,
        Request::builder()
            .method("PROPFIND")
            .uri("/dav/calendars/user01/")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .header("Depth", "1")
            .body(propfind_body(&["resourcetype", "displayname"]))
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::MULTI_STATUS);
    let body = common::body_text(response).await;
    assert!(body.contains("<D:href>/dav/calendars/user01/</D:href>"));
    assert!(body.contains("<D:href>/dav/calendars/user01/personal/</D:href>"));
    assert!(body.contains("<D:displayname>Personal</D:displayname>"));
    assert!(body.contains("<D:resourcetype><D:collection/><C:calendar/></D:resourcetype>"));
    Ok(())
}

#[sqlx::test]
async fn http_propfind_calendar_collection_returns_supported_components(
    pool: PgPool,
) -> sqlx::Result<()> {
    let user = common::create_user_with_password(&pool, "user01", "user01").await?;
    davhome::models::calendar::create_calendar(&pool, user.id, "personal", "Personal", None)
        .await?;
    let app = common::app(pool);

    let response = common::send(
        app,
        Request::builder()
            .method("PROPFIND")
            .uri("/dav/calendars/user01/personal/")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .body(calendar_propfind_body(&[
                "supported-calendar-component-set",
            ]))
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::MULTI_STATUS);
    let body = common::body_text(response).await;
    assert!(body.contains("<D:href>/dav/calendars/user01/personal/</D:href>"));
    assert!(body.contains("<C:supported-calendar-component-set>"));
    assert!(body.contains("<C:comp name=\"VEVENT\"/>"));
    assert!(body.contains("<C:comp name=\"VTODO\"/>"));
    Ok(())
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
