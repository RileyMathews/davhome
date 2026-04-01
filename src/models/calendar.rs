use sqlx::PgPool;
use uuid::Uuid;

#[derive(Debug, Clone)]
pub struct Calendar {
    pub id: Uuid,
    pub owner_user_id: Uuid,
    pub supported_component_set: Vec<String>,
    pub max_resource_size: Option<i64>,
    pub min_date_at: Option<chrono::DateTime<chrono::Utc>>,
    pub max_date_at: Option<chrono::DateTime<chrono::Utc>>,
    pub max_instances: Option<i32>,
    pub max_attendees_per_instance: Option<i32>,
    pub calendar_timezone: Option<String>,
    pub calendar_timezone_id: Option<String>,
    pub extra_properties: serde_json::Value,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

#[derive(Debug, Clone)]
pub struct CalendarBinding {
    pub id: Uuid,
    pub calendar_id: Uuid,
    pub principal_user_id: Uuid,
    pub uri: String,
    pub displayname: Option<String>,
    pub calendar_description: Option<String>,
    pub calendar_timezone: Option<String>,
    pub calendar_timezone_id: Option<String>,
    pub schedule_transparency: String,
    pub calendar_color: Option<String>,
    pub calendar_order: i32,
    pub extra_properties: serde_json::Value,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

/// Represents a calendar with its owner binding for display in the UI
#[derive(Debug, Clone)]
pub struct CalendarWithBinding {
    pub id: Uuid,
    pub uri: String,
    pub displayname: Option<String>,
    pub calendar_description: Option<String>,
    pub supported_component_set: Vec<String>,
    pub created_at: chrono::DateTime<chrono::Utc>,
}

/// Create a new calendar with an owner binding
/// Returns the created calendar ID
pub async fn create_calendar(
    pool: &PgPool,
    owner_user_id: Uuid,
    binding_uri: &str,
    displayname: &str,
    calendar_description: Option<&str>,
) -> Result<Uuid, sqlx::Error> {
    let mut txn = pool.begin().await?;

    let calendar_id = Uuid::new_v4();

    sqlx::query!(
        r#"
        INSERT INTO calendars (
            id, owner_user_id, supported_component_set, extra_properties
        ) VALUES ($1, $2, ARRAY['VEVENT', 'VTODO']::calendar_component[], $3)
        "#,
        calendar_id,
        owner_user_id,
        serde_json::json!({})
    )
    .execute(&mut *txn)
    .await?;

    sqlx::query!(
        r#"
        INSERT INTO calendar_bindings (
            calendar_id, principal_user_id, uri, displayname, calendar_description, 
            extra_properties, schedule_transparency
        ) VALUES ($1, $2, $3, $4, $5, $6, 'opaque'::schedule_transparency)
        "#,
        calendar_id,
        owner_user_id,
        binding_uri,
        Some(displayname),
        calendar_description,
        serde_json::json!({})
    )
    .execute(&mut *txn)
    .await?;

    txn.commit().await?;

    Ok(calendar_id)
}

/// List calendars visible to a user via their bindings
pub async fn list_user_calendars(
    pool: &PgPool,
    principal_user_id: Uuid,
) -> Result<Vec<CalendarWithBinding>, sqlx::Error> {
    let calendars = sqlx::query_as!(
        CalendarWithBinding,
        r#"
        SELECT 
            c.id,
            cb.uri,
            cb.displayname,
            cb.calendar_description,
            c.supported_component_set AS "supported_component_set!: Vec<String>",
            c.created_at
        FROM calendars c
        JOIN calendar_bindings cb ON c.id = cb.calendar_id
        WHERE cb.principal_user_id = $1
        AND c.deleted_at IS NULL
        ORDER BY cb.calendar_order, c.created_at DESC
        "#,
        principal_user_id
    )
    .fetch_all(pool)
    .await?;

    Ok(calendars)
}

/// Delete a calendar if the user is the owner
/// Returns true if a calendar was deleted
pub async fn delete_calendar_if_owner(
    pool: &PgPool,
    calendar_id: Uuid,
    user_id: Uuid,
) -> Result<bool, sqlx::Error> {
    let result: Option<sqlx::postgres::PgRow> = sqlx::query(
        r#"
        DELETE FROM calendars
        WHERE id = $1 AND owner_user_id = $2
        RETURNING id
        "#,
    )
    .bind(calendar_id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?;

    Ok(result.is_some())
}

/// Delete a calendar by binding URI when the authenticated user is the owner.
pub async fn delete_calendar_by_uri_if_owner(
    pool: &PgPool,
    user_id: Uuid,
    binding_uri: &str,
) -> Result<bool, sqlx::Error> {
    let result: Option<sqlx::postgres::PgRow> = sqlx::query(
        r#"
        DELETE FROM calendars c
        USING calendar_bindings cb
        WHERE c.id = cb.calendar_id
          AND c.owner_user_id = $1
          AND cb.principal_user_id = $1
          AND cb.uri = $2
        RETURNING c.id
        "#,
    )
    .bind(user_id)
    .bind(binding_uri)
    .fetch_optional(pool)
    .await?;

    Ok(result.is_some())
}

/// Check if a user owns a calendar
pub async fn is_calendar_owner(
    pool: &PgPool,
    calendar_id: Uuid,
    user_id: Uuid,
) -> Result<bool, sqlx::Error> {
    let result: Option<sqlx::postgres::PgRow> = sqlx::query(
        r#"
        SELECT id FROM calendars
        WHERE id = $1 AND owner_user_id = $2
        "#,
    )
    .bind(calendar_id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?;

    Ok(result.is_some())
}
