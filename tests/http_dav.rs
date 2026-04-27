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

#[sqlx::test]
async fn http_put_calendar_object_creates_event(pool: PgPool) -> sqlx::Result<()> {
    let user = common::create_user_with_password(&pool, "user01", "user01").await?;
    davhome::models::calendar::create_calendar(&pool, user.id, "personal", "Personal", None)
        .await?;
    let app = common::app(pool.clone());

    let response = common::send(
        app,
        Request::builder()
            .method("PUT")
            .uri("/dav/calendars/user01/personal/event.ics")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .header(header::CONTENT_TYPE, "text/calendar")
            .header(header::IF_NONE_MATCH, "*")
            .body(Body::from(event_ics("event-1", "First")))
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::CREATED);
    assert!(response.headers().get(header::ETAG).is_some());

    let binding = davhome::models::calendar::find_dav_calendar_binding(&pool, user.id, "personal")
        .await?
        .unwrap();
    let object = davhome::models::calendar::find_dav_calendar_object(
        &pool,
        binding.calendar_id,
        "event.ics",
    )
    .await?
    .unwrap();
    assert_eq!(object.uid, "event-1");
    assert_eq!(object.component_type, "VEVENT");
    Ok(())
}

#[sqlx::test]
async fn http_get_calendar_object_returns_icalendar_with_cache_headers(
    pool: PgPool,
) -> sqlx::Result<()> {
    let user = common::create_user_with_password(&pool, "user01", "user01").await?;
    davhome::models::calendar::create_calendar(&pool, user.id, "personal", "Personal", None)
        .await?;
    let app = common::app(pool);
    let ics = event_ics("event-1", "First");

    let put_response = common::send(
        app.clone(),
        Request::builder()
            .method("PUT")
            .uri("/dav/calendars/user01/personal/event.ics")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .header(header::CONTENT_TYPE, "text/calendar")
            .body(Body::from(ics.clone()))
            .unwrap(),
    )
    .await;
    let etag = put_response.headers().get(header::ETAG).unwrap().clone();

    let response = common::send(
        app,
        Request::builder()
            .method("GET")
            .uri("/dav/calendars/user01/personal/event.ics")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .body(Body::empty())
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.headers().get(header::ETAG).unwrap(), &etag);
    assert!(response.headers().get(header::LAST_MODIFIED).is_some());
    assert_eq!(
        response.headers().get(header::CONTENT_TYPE).unwrap(),
        "text/calendar;charset=utf-8;component=VEVENT"
    );
    assert_eq!(common::body_text(response).await, ics);
    Ok(())
}

#[sqlx::test]
async fn http_get_calendar_object_preserves_vtimezone(pool: PgPool) -> sqlx::Result<()> {
    let user = common::create_user_with_password(&pool, "user01", "user01").await?;
    davhome::models::calendar::create_calendar(&pool, user.id, "personal", "Personal", None)
        .await?;
    let app = common::app(pool);
    let ics = event_with_timezone_ics("event-1", "With TZ");

    common::send(
        app.clone(),
        Request::builder()
            .method("PUT")
            .uri("/dav/calendars/user01/personal/event.ics")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .header(header::CONTENT_TYPE, "text/calendar")
            .body(Body::from(ics.clone()))
            .unwrap(),
    )
    .await;

    let response = common::send(
        app,
        Request::builder()
            .method("GET")
            .uri("/dav/calendars/user01/personal/event.ics")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .body(Body::empty())
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(common::body_text(response).await, ics);
    Ok(())
}

#[sqlx::test]
async fn http_head_calendar_object_returns_headers_without_body(pool: PgPool) -> sqlx::Result<()> {
    let user = common::create_user_with_password(&pool, "user01", "user01").await?;
    davhome::models::calendar::create_calendar(&pool, user.id, "personal", "Personal", None)
        .await?;
    let app = common::app(pool);

    common::send(
        app.clone(),
        Request::builder()
            .method("PUT")
            .uri("/dav/calendars/user01/personal/event.ics")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .header(header::CONTENT_TYPE, "text/calendar")
            .body(Body::from(event_ics("event-1", "First")))
            .unwrap(),
    )
    .await;

    let response = common::send(
        app,
        Request::builder()
            .method("HEAD")
            .uri("/dav/calendars/user01/personal/event.ics")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .body(Body::empty())
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::OK);
    assert!(response.headers().get(header::ETAG).is_some());
    assert_eq!(common::body_text(response).await, "");
    Ok(())
}

#[sqlx::test]
async fn http_propfind_calendar_object_returns_dav_metadata(pool: PgPool) -> sqlx::Result<()> {
    let user = common::create_user_with_password(&pool, "user01", "user01").await?;
    davhome::models::calendar::create_calendar(&pool, user.id, "personal", "Personal", None)
        .await?;
    let app = common::app(pool);
    let ics = event_ics("event-1", "First");

    let put_response = common::send(
        app.clone(),
        Request::builder()
            .method("PUT")
            .uri("/dav/calendars/user01/personal/event.ics")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .header(header::CONTENT_TYPE, "text/calendar")
            .body(Body::from(ics.clone()))
            .unwrap(),
    )
    .await;
    let etag = put_response
        .headers()
        .get(header::ETAG)
        .unwrap()
        .to_str()
        .unwrap()
        .to_string();

    let response = common::send(
        app,
        Request::builder()
            .method("PROPFIND")
            .uri("/dav/calendars/user01/personal/event.ics")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .body(propfind_body(&[
                "getetag",
                "getlastmodified",
                "getcontenttype",
                "getcontentlength",
                "resourcetype",
            ]))
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::MULTI_STATUS);
    let body = common::body_text(response).await;
    assert!(body.contains("<D:href>/dav/calendars/user01/personal/event.ics</D:href>"));
    assert!(body.contains(&format!("<D:getetag>{etag}</D:getetag>")));
    assert!(body.contains(
        "<D:getcontenttype>text/calendar;charset=utf-8;component=VEVENT</D:getcontenttype>"
    ));
    assert!(body.contains(&format!(
        "<D:getcontentlength>{}</D:getcontentlength>",
        ics.len()
    )));
    assert!(body.contains("<D:resourcetype></D:resourcetype>"));
    Ok(())
}

#[sqlx::test]
async fn http_put_calendar_object_if_none_match_existing_precondition_fails(
    pool: PgPool,
) -> sqlx::Result<()> {
    let user = common::create_user_with_password(&pool, "user01", "user01").await?;
    davhome::models::calendar::create_calendar(&pool, user.id, "personal", "Personal", None)
        .await?;
    let app = common::app(pool);

    common::send(
        app.clone(),
        Request::builder()
            .method("PUT")
            .uri("/dav/calendars/user01/personal/event.ics")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .header(header::CONTENT_TYPE, "text/calendar")
            .body(Body::from(event_ics("event-1", "First")))
            .unwrap(),
    )
    .await;

    let response = common::send(
        app,
        Request::builder()
            .method("PUT")
            .uri("/dav/calendars/user01/personal/event.ics")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .header(header::CONTENT_TYPE, "text/calendar")
            .header(header::IF_NONE_MATCH, "*")
            .body(Body::from(event_ics("event-1", "Updated")))
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::PRECONDITION_FAILED);
    Ok(())
}

#[sqlx::test]
async fn http_put_calendar_object_updates_with_matching_if_match(pool: PgPool) -> sqlx::Result<()> {
    let user = common::create_user_with_password(&pool, "user01", "user01").await?;
    davhome::models::calendar::create_calendar(&pool, user.id, "personal", "Personal", None)
        .await?;
    let app = common::app(pool);

    let put_response = common::send(
        app.clone(),
        Request::builder()
            .method("PUT")
            .uri("/dav/calendars/user01/personal/event.ics")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .header(header::CONTENT_TYPE, "text/calendar")
            .body(Body::from(event_ics("event-1", "First")))
            .unwrap(),
    )
    .await;
    let etag = put_response.headers().get(header::ETAG).unwrap().clone();

    let response = common::send(
        app,
        Request::builder()
            .method("PUT")
            .uri("/dav/calendars/user01/personal/event.ics")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .header(header::CONTENT_TYPE, "text/calendar")
            .header(header::IF_MATCH, etag)
            .body(Body::from(event_ics("event-1", "Updated")))
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::NO_CONTENT);
    assert!(response.headers().get(header::ETAG).is_some());
    Ok(())
}

#[sqlx::test]
async fn http_put_calendar_object_accepts_vtodo(pool: PgPool) -> sqlx::Result<()> {
    let user = common::create_user_with_password(&pool, "user01", "user01").await?;
    davhome::models::calendar::create_calendar(&pool, user.id, "personal", "Personal", None)
        .await?;
    let app = common::app(pool.clone());

    let response = common::send(
        app,
        Request::builder()
            .method("PUT")
            .uri("/dav/calendars/user01/personal/task.ics")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .header(header::CONTENT_TYPE, "text/calendar")
            .body(Body::from(todo_ics("task-1", "Task")))
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::CREATED);

    let binding = davhome::models::calendar::find_dav_calendar_binding(&pool, user.id, "personal")
        .await?
        .unwrap();
    let object =
        davhome::models::calendar::find_dav_calendar_object(&pool, binding.calendar_id, "task.ics")
            .await?
            .unwrap();
    assert_eq!(object.component_type, "VTODO");
    Ok(())
}

#[sqlx::test]
async fn http_put_calendar_object_rejects_unsupported_component(pool: PgPool) -> sqlx::Result<()> {
    let user = common::create_user_with_password(&pool, "user01", "user01").await?;
    davhome::models::calendar::create_calendar(&pool, user.id, "personal", "Personal", None)
        .await?;
    let app = common::app(pool);

    let response = common::send(
        app,
        Request::builder()
            .method("PUT")
            .uri("/dav/calendars/user01/personal/journal.ics")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .header(header::CONTENT_TYPE, "text/calendar")
            .body(Body::from(journal_ics("journal-1", "Journal")))
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    Ok(())
}

#[sqlx::test]
async fn http_put_calendar_object_duplicate_uid_returns_conflict(pool: PgPool) -> sqlx::Result<()> {
    let user = common::create_user_with_password(&pool, "user01", "user01").await?;
    davhome::models::calendar::create_calendar(&pool, user.id, "personal", "Personal", None)
        .await?;
    let app = common::app(pool);

    common::send(
        app.clone(),
        Request::builder()
            .method("PUT")
            .uri("/dav/calendars/user01/personal/first.ics")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .header(header::CONTENT_TYPE, "text/calendar")
            .body(Body::from(event_ics("same-uid", "First")))
            .unwrap(),
    )
    .await;

    let response = common::send(
        app,
        Request::builder()
            .method("PUT")
            .uri("/dav/calendars/user01/personal/second.ics")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .header(header::CONTENT_TYPE, "text/calendar")
            .body(Body::from(event_ics("same-uid", "Second")))
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::CONFLICT);
    Ok(())
}

#[sqlx::test]
async fn http_delete_calendar_object_removes_event(pool: PgPool) -> sqlx::Result<()> {
    let user = common::create_user_with_password(&pool, "user01", "user01").await?;
    davhome::models::calendar::create_calendar(&pool, user.id, "personal", "Personal", None)
        .await?;
    let app = common::app(pool.clone());

    common::send(
        app.clone(),
        Request::builder()
            .method("PUT")
            .uri("/dav/calendars/user01/personal/event.ics")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .header(header::CONTENT_TYPE, "text/calendar")
            .body(Body::from(event_ics("event-1", "First")))
            .unwrap(),
    )
    .await;

    let response = common::send(
        app,
        Request::builder()
            .method("DELETE")
            .uri("/dav/calendars/user01/personal/event.ics")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .body(Body::empty())
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::NO_CONTENT);

    let binding = davhome::models::calendar::find_dav_calendar_binding(&pool, user.id, "personal")
        .await?
        .unwrap();
    assert!(
        davhome::models::calendar::find_dav_calendar_object(
            &pool,
            binding.calendar_id,
            "event.ics"
        )
        .await?
        .is_none()
    );
    Ok(())
}

#[sqlx::test]
async fn http_delete_calendar_object_with_stale_if_match_precondition_fails(
    pool: PgPool,
) -> sqlx::Result<()> {
    let user = common::create_user_with_password(&pool, "user01", "user01").await?;
    davhome::models::calendar::create_calendar(&pool, user.id, "personal", "Personal", None)
        .await?;
    let app = common::app(pool);

    common::send(
        app.clone(),
        Request::builder()
            .method("PUT")
            .uri("/dav/calendars/user01/personal/event.ics")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .header(header::CONTENT_TYPE, "text/calendar")
            .body(Body::from(event_ics("event-1", "First")))
            .unwrap(),
    )
    .await;

    let response = common::send(
        app,
        Request::builder()
            .method("DELETE")
            .uri("/dav/calendars/user01/personal/event.ics")
            .header(header::AUTHORIZATION, basic_auth("user01", "user01"))
            .header(header::IF_MATCH, "\"stale\"")
            .body(Body::empty())
            .unwrap(),
    )
    .await;

    assert_eq!(response.status(), StatusCode::PRECONDITION_FAILED);
    Ok(())
}

fn event_ics(uid: &str, summary: &str) -> String {
    format!(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//davhome tests//EN\r\nBEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20260427T120000Z\r\nDTSTART:20260427T120000Z\r\nSUMMARY:{summary}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
}

fn event_with_timezone_ics(uid: &str, summary: &str) -> String {
    format!(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//davhome tests//EN\r\nBEGIN:VTIMEZONE\r\nTZID:America/New_York\r\nBEGIN:STANDARD\r\nDTSTART:20241103T020000\r\nTZOFFSETFROM:-0400\r\nTZOFFSETTO:-0500\r\nTZNAME:EST\r\nEND:STANDARD\r\nEND:VTIMEZONE\r\nBEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20260427T120000Z\r\nDTSTART;TZID=America/New_York:20260427T120000\r\nSUMMARY:{summary}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
}

fn todo_ics(uid: &str, summary: &str) -> String {
    format!(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//davhome tests//EN\r\nBEGIN:VTODO\r\nUID:{uid}\r\nDTSTAMP:20260427T120000Z\r\nSUMMARY:{summary}\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
    )
}

fn journal_ics(uid: &str, summary: &str) -> String {
    format!(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//davhome tests//EN\r\nBEGIN:VJOURNAL\r\nUID:{uid}\r\nDTSTAMP:20260427T120000Z\r\nSUMMARY:{summary}\r\nEND:VJOURNAL\r\nEND:VCALENDAR\r\n"
    )
}
