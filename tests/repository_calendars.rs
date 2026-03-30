use davhome::models::calendar;
use sqlx::PgPool;
use uuid::Uuid;

mod common;

#[sqlx::test]
async fn repository_create_calendar_inserts_calendar_and_binding(pool: PgPool) -> sqlx::Result<()> {
    let owner = common::create_user_with_password(&pool, "owner", "passwordpassword").await?;

    let calendar_id =
        calendar::create_calendar(&pool, owner.id, "Personal", Some("Main calendar")).await?;

    let calendar_count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM calendars WHERE id = $1")
        .bind(calendar_id)
        .fetch_one(&pool)
        .await?;
    let binding_count: i64 =
        sqlx::query_scalar("SELECT COUNT(*) FROM calendar_bindings WHERE calendar_id = $1")
            .bind(calendar_id)
            .fetch_one(&pool)
            .await?;

    assert_eq!(calendar_count, 1);
    assert_eq!(binding_count, 1);
    Ok(())
}

#[sqlx::test]
async fn repository_create_calendar_sets_default_component_set(pool: PgPool) -> sqlx::Result<()> {
    let owner = common::create_user_with_password(&pool, "owner2", "passwordpassword").await?;

    let calendar_id = calendar::create_calendar(&pool, owner.id, "Tasks", None).await?;

    let supported_components: Vec<String> = sqlx::query_scalar(
        "SELECT unnest(supported_component_set)::text FROM calendars WHERE id = $1 ORDER BY 1",
    )
    .bind(calendar_id)
    .fetch_all(&pool)
    .await?;

    assert_eq!(supported_components, vec!["VEVENT", "VTODO"]);
    Ok(())
}

#[sqlx::test]
async fn repository_create_calendar_generates_uuid_binding_uri(pool: PgPool) -> sqlx::Result<()> {
    let owner = common::create_user_with_password(&pool, "owner3", "passwordpassword").await?;

    let calendar_id = calendar::create_calendar(&pool, owner.id, "Work", None).await?;

    let uri: String = sqlx::query_scalar(
        "SELECT uri FROM calendar_bindings WHERE calendar_id = $1 AND principal_user_id = $2",
    )
    .bind(calendar_id)
    .bind(owner.id)
    .fetch_one(&pool)
    .await?;

    assert!(Uuid::parse_str(&uri).is_ok());
    Ok(())
}

#[sqlx::test]
async fn repository_list_user_calendars_only_returns_visible_rows(
    pool: PgPool,
) -> sqlx::Result<()> {
    let owner = common::create_user_with_password(&pool, "owner4", "passwordpassword").await?;
    let other = common::create_user_with_password(&pool, "other4", "passwordpassword").await?;

    let visible_id = calendar::create_calendar(&pool, owner.id, "Visible", None).await?;
    let hidden_id = calendar::create_calendar(&pool, other.id, "Hidden", None).await?;

    let results = calendar::list_user_calendars(&pool, owner.id).await?;

    assert_eq!(results.len(), 1);
    assert_eq!(results[0].id, visible_id);
    assert_ne!(results[0].id, hidden_id);
    Ok(())
}

#[sqlx::test]
async fn repository_list_user_calendars_orders_by_calendar_order(pool: PgPool) -> sqlx::Result<()> {
    let owner = common::create_user_with_password(&pool, "owner5", "passwordpassword").await?;

    let first_id = calendar::create_calendar(&pool, owner.id, "First", None).await?;
    let second_id = calendar::create_calendar(&pool, owner.id, "Second", None).await?;

    sqlx::query("UPDATE calendar_bindings SET calendar_order = 5 WHERE calendar_id = $1")
        .bind(first_id)
        .execute(&pool)
        .await?;
    sqlx::query("UPDATE calendar_bindings SET calendar_order = 1 WHERE calendar_id = $1")
        .bind(second_id)
        .execute(&pool)
        .await?;

    let results = calendar::list_user_calendars(&pool, owner.id).await?;

    assert_eq!(results.len(), 2);
    assert_eq!(results[0].id, second_id);
    assert_eq!(results[1].id, first_id);
    Ok(())
}

#[sqlx::test]
async fn repository_delete_calendar_if_owner_deletes_owned_calendar(
    pool: PgPool,
) -> sqlx::Result<()> {
    let owner = common::create_user_with_password(&pool, "owner6", "passwordpassword").await?;

    let calendar_id = calendar::create_calendar(&pool, owner.id, "Delete Me", None).await?;
    let deleted = calendar::delete_calendar_if_owner(&pool, calendar_id, owner.id).await?;

    let remaining: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM calendars WHERE id = $1")
        .bind(calendar_id)
        .fetch_one(&pool)
        .await?;

    assert!(deleted);
    assert_eq!(remaining, 0);
    Ok(())
}

#[sqlx::test]
async fn repository_delete_calendar_if_owner_rejects_non_owner(pool: PgPool) -> sqlx::Result<()> {
    let owner = common::create_user_with_password(&pool, "owner7", "passwordpassword").await?;
    let other = common::create_user_with_password(&pool, "other7", "passwordpassword").await?;

    let calendar_id = calendar::create_calendar(&pool, owner.id, "Keep Me", None).await?;
    let deleted = calendar::delete_calendar_if_owner(&pool, calendar_id, other.id).await?;

    let remaining: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM calendars WHERE id = $1")
        .bind(calendar_id)
        .fetch_one(&pool)
        .await?;

    assert!(!deleted);
    assert_eq!(remaining, 1);
    Ok(())
}

#[sqlx::test]
async fn repository_delete_calendar_cascades_to_owner_binding(pool: PgPool) -> sqlx::Result<()> {
    let owner = common::create_user_with_password(&pool, "owner8", "passwordpassword").await?;

    let calendar_id = calendar::create_calendar(&pool, owner.id, "Cascade", None).await?;
    calendar::delete_calendar_if_owner(&pool, calendar_id, owner.id).await?;

    let bindings_remaining: i64 =
        sqlx::query_scalar("SELECT COUNT(*) FROM calendar_bindings WHERE calendar_id = $1")
            .bind(calendar_id)
            .fetch_one(&pool)
            .await?;

    assert_eq!(bindings_remaining, 0);
    Ok(())
}

#[sqlx::test]
async fn repository_is_calendar_owner_matches_owner_state(pool: PgPool) -> sqlx::Result<()> {
    let owner = common::create_user_with_password(&pool, "owner9", "passwordpassword").await?;
    let other = common::create_user_with_password(&pool, "other9", "passwordpassword").await?;

    let calendar_id = calendar::create_calendar(&pool, owner.id, "Ownership", None).await?;

    assert!(calendar::is_calendar_owner(&pool, calendar_id, owner.id).await?);
    assert!(!calendar::is_calendar_owner(&pool, calendar_id, other.id).await?);
    Ok(())
}
