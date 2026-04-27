use sqlx::{PgPool, Postgres, Transaction};
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

#[derive(Debug, Clone, sqlx::FromRow)]
pub struct DavCalendarBinding {
    pub id: Uuid,
    pub calendar_id: Uuid,
    pub owner_user_id: Uuid,
    pub uri: String,
    pub displayname: Option<String>,
    pub calendar_description: Option<String>,
    pub supported_component_set: Vec<String>,
}

#[derive(Debug, Clone, sqlx::FromRow)]
pub struct DavCalendarObject {
    pub id: Uuid,
    pub calendar_id: Uuid,
    pub href: String,
    pub uid: String,
    pub component_type: String,
    pub icalendar: String,
    pub etag: String,
    pub last_modified_at: chrono::DateTime<chrono::Utc>,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

#[derive(Debug, Clone)]
pub struct CalendarObjectInput {
    pub href: String,
    pub uid: String,
    pub component_type: String,
    pub icalendar: String,
    pub etag: String,
}

#[derive(Debug)]
pub enum CalendarObjectWriteResult {
    Created(DavCalendarObject),
    Updated(DavCalendarObject),
    Unchanged(DavCalendarObject),
}

#[derive(Debug)]
pub enum CalendarObjectWriteError {
    Sqlx(sqlx::Error),
    UidConflict { href: String },
}

impl From<sqlx::Error> for CalendarObjectWriteError {
    fn from(error: sqlx::Error) -> Self {
        Self::Sqlx(error)
    }
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

pub async fn list_dav_calendar_bindings(
    pool: &PgPool,
    principal_user_id: Uuid,
) -> Result<Vec<DavCalendarBinding>, sqlx::Error> {
    sqlx::query_as::<_, DavCalendarBinding>(
        r#"
        SELECT
            cb.id,
            c.id AS calendar_id,
            c.owner_user_id,
            cb.uri,
            cb.displayname,
            cb.calendar_description,
            c.supported_component_set::text[] AS supported_component_set
        FROM calendars c
        JOIN calendar_bindings cb ON c.id = cb.calendar_id
        WHERE cb.principal_user_id = $1
        AND c.deleted_at IS NULL
        ORDER BY cb.calendar_order, c.created_at DESC
        "#,
    )
    .bind(principal_user_id)
    .fetch_all(pool)
    .await
}

pub async fn find_dav_calendar_binding(
    pool: &PgPool,
    principal_user_id: Uuid,
    binding_uri: &str,
) -> Result<Option<DavCalendarBinding>, sqlx::Error> {
    sqlx::query_as::<_, DavCalendarBinding>(
        r#"
        SELECT
            cb.id,
            c.id AS calendar_id,
            c.owner_user_id,
            cb.uri,
            cb.displayname,
            cb.calendar_description,
            c.supported_component_set::text[] AS supported_component_set
        FROM calendars c
        JOIN calendar_bindings cb ON c.id = cb.calendar_id
        WHERE cb.principal_user_id = $1
        AND cb.uri = $2
        AND c.deleted_at IS NULL
        "#,
    )
    .bind(principal_user_id)
    .bind(binding_uri)
    .fetch_optional(pool)
    .await
}

pub async fn find_dav_calendar_object(
    pool: &PgPool,
    calendar_id: Uuid,
    href: &str,
) -> Result<Option<DavCalendarObject>, sqlx::Error> {
    sqlx::query_as::<_, DavCalendarObject>(
        r#"
        SELECT
            id,
            calendar_id,
            href,
            uid,
            component_type::text AS component_type,
            icalendar,
            etag,
            last_modified_at,
            created_at,
            updated_at
        FROM calendar_objects
        WHERE calendar_id = $1
        AND href = $2
        "#,
    )
    .bind(calendar_id)
    .bind(href)
    .fetch_optional(pool)
    .await
}

pub async fn list_dav_calendar_objects(
    pool: &PgPool,
    calendar_id: Uuid,
) -> Result<Vec<DavCalendarObject>, sqlx::Error> {
    sqlx::query_as::<_, DavCalendarObject>(
        r#"
        SELECT
            id,
            calendar_id,
            href,
            uid,
            component_type::text AS component_type,
            icalendar,
            etag,
            last_modified_at,
            created_at,
            updated_at
        FROM calendar_objects
        WHERE calendar_id = $1
        ORDER BY href
        "#,
    )
    .bind(calendar_id)
    .fetch_all(pool)
    .await
}

pub async fn put_dav_calendar_object(
    pool: &PgPool,
    calendar_id: Uuid,
    binding_id: Uuid,
    changed_by_user_id: Uuid,
    input: CalendarObjectInput,
) -> Result<CalendarObjectWriteResult, CalendarObjectWriteError> {
    let mut txn = pool.begin().await?;

    let existing = select_calendar_object_for_update(&mut txn, calendar_id, &input.href).await?;

    if let Some(existing) = &existing
        && existing.uid != input.uid
    {
        return Err(CalendarObjectWriteError::UidConflict {
            href: existing.href.clone(),
        });
    }

    if let Some(conflicting_href) =
        find_uid_conflict(&mut txn, calendar_id, &input.uid, &input.href).await?
    {
        return Err(CalendarObjectWriteError::UidConflict {
            href: conflicting_href,
        });
    }

    let result = if let Some(existing) = existing {
        if existing.etag == input.etag
            && existing.icalendar == input.icalendar
            && existing.component_type == input.component_type
        {
            CalendarObjectWriteResult::Unchanged(existing)
        } else {
            let object = sqlx::query_as::<_, DavCalendarObject>(
                r#"
                UPDATE calendar_objects
                SET uid = $3,
                    component_type = $4::calendar_component,
                    icalendar = $5,
                    etag = $6,
                    last_modified_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE calendar_id = $1
                AND href = $2
                RETURNING
                    id,
                    calendar_id,
                    href,
                    uid,
                    component_type::text AS component_type,
                    icalendar,
                    etag,
                    last_modified_at,
                    created_at,
                    updated_at
                "#,
            )
            .bind(calendar_id)
            .bind(&input.href)
            .bind(&input.uid)
            .bind(&input.component_type)
            .bind(&input.icalendar)
            .bind(&input.etag)
            .fetch_one(&mut *txn)
            .await?;

            record_calendar_change(
                &mut txn,
                calendar_id,
                changed_by_user_id,
                binding_id,
                &input.href,
                2,
            )
            .await?;

            CalendarObjectWriteResult::Updated(object)
        }
    } else {
        let object = sqlx::query_as::<_, DavCalendarObject>(
            r#"
            INSERT INTO calendar_objects (
                calendar_id, href, uid, component_type, icalendar, etag
            ) VALUES ($1, $2, $3, $4::calendar_component, $5, $6)
            RETURNING
                id,
                calendar_id,
                href,
                uid,
                component_type::text AS component_type,
                icalendar,
                etag,
                last_modified_at,
                created_at,
                updated_at
            "#,
        )
        .bind(calendar_id)
        .bind(&input.href)
        .bind(&input.uid)
        .bind(&input.component_type)
        .bind(&input.icalendar)
        .bind(&input.etag)
        .fetch_one(&mut *txn)
        .await?;

        record_calendar_change(
            &mut txn,
            calendar_id,
            changed_by_user_id,
            binding_id,
            &input.href,
            1,
        )
        .await?;

        CalendarObjectWriteResult::Created(object)
    };

    txn.commit().await?;
    Ok(result)
}

pub async fn delete_dav_calendar_object(
    pool: &PgPool,
    calendar_id: Uuid,
    binding_id: Uuid,
    changed_by_user_id: Uuid,
    href: &str,
) -> Result<Option<DavCalendarObject>, sqlx::Error> {
    let mut txn = pool.begin().await?;
    let existing = select_calendar_object_for_update(&mut txn, calendar_id, href).await?;

    let Some(object) = existing else {
        return Ok(None);
    };

    sqlx::query(
        r#"
        DELETE FROM calendar_objects
        WHERE id = $1
        "#,
    )
    .bind(object.id)
    .execute(&mut *txn)
    .await?;

    record_calendar_change(
        &mut txn,
        calendar_id,
        changed_by_user_id,
        binding_id,
        href,
        3,
    )
    .await?;

    txn.commit().await?;
    Ok(Some(object))
}

async fn select_calendar_object_for_update(
    txn: &mut Transaction<'_, Postgres>,
    calendar_id: Uuid,
    href: &str,
) -> Result<Option<DavCalendarObject>, sqlx::Error> {
    sqlx::query_as::<_, DavCalendarObject>(
        r#"
        SELECT
            id,
            calendar_id,
            href,
            uid,
            component_type::text AS component_type,
            icalendar,
            etag,
            last_modified_at,
            created_at,
            updated_at
        FROM calendar_objects
        WHERE calendar_id = $1
        AND href = $2
        FOR UPDATE
        "#,
    )
    .bind(calendar_id)
    .bind(href)
    .fetch_optional(&mut **txn)
    .await
}

async fn find_uid_conflict(
    txn: &mut Transaction<'_, Postgres>,
    calendar_id: Uuid,
    uid: &str,
    href: &str,
) -> Result<Option<String>, sqlx::Error> {
    sqlx::query_scalar(
        r#"
        SELECT href
        FROM calendar_objects
        WHERE calendar_id = $1
        AND uid = $2
        AND href <> $3
        "#,
    )
    .bind(calendar_id)
    .bind(uid)
    .bind(href)
    .fetch_optional(&mut **txn)
    .await
}

async fn record_calendar_change(
    txn: &mut Transaction<'_, Postgres>,
    calendar_id: Uuid,
    changed_by_user_id: Uuid,
    binding_id: Uuid,
    object_uri: &str,
    operation: i16,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"
        INSERT INTO calendar_changes (
            calendar_id, sync_token, changed_by_user_id, binding_id, object_uri, operation
        ) VALUES (
            $1,
            (SELECT COALESCE(MAX(sync_token), 0) + 1 FROM calendar_changes WHERE calendar_id = $1),
            $2,
            $3,
            $4,
            $5
        )
        "#,
    )
    .bind(calendar_id)
    .bind(changed_by_user_id)
    .bind(binding_id)
    .bind(object_uri)
    .bind(operation)
    .execute(&mut **txn)
    .await?;

    Ok(())
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
