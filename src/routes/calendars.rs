use askama::Template;
use axum::{
    extract::{Form, State},
    response::Html,
};
use serde::Deserialize;
use sqlx::PgPool;
use tower_cookies::Cookies;
use uuid::Uuid;

use crate::auth::require_auth;
use crate::forms::{FormErrors, validate_required};
use crate::models::calendar;

#[derive(Deserialize, Default, Clone)]
pub struct CreateCalendarForm {
    pub displayname: String,
    pub description: Option<String>,
}

#[derive(Template)]
#[template(path = "calendars.html")]
pub struct CalendarsTemplate {
    pub username: String,
    pub calendars: Vec<calendar::CalendarWithBinding>,
    pub form: CreateCalendarForm,
    pub errors: FormErrors,
}

/// Display the calendar management page
pub async fn calendars_page(State(pool): State<PgPool>, cookies: Cookies) -> Html<String> {
    let user = match require_auth(&pool, &cookies).await {
        Ok(u) => u,
        Err(redirect) => return redirect,
    };

    let calendars: Vec<calendar::CalendarWithBinding> =
        calendar::list_user_calendars(&pool, user.id)
            .await
            .unwrap_or_default();

    let template = CalendarsTemplate {
        username: user.username,
        calendars,
        form: CreateCalendarForm::default(),
        errors: FormErrors::new(),
    };

    Html(template.render().unwrap())
}

/// Handle calendar creation
pub async fn handle_create_calendar(
    State(pool): State<PgPool>,
    cookies: Cookies,
    Form(form): Form<CreateCalendarForm>,
) -> Html<String> {
    let user = match require_auth(&pool, &cookies).await {
        Ok(u) => u,
        Err(redirect) => return redirect,
    };

    let mut errors = FormErrors::new();

    // Validate required fields
    validate_required(&form.displayname, "displayname", &mut errors);

    // If validation errors exist, return form with errors
    if errors.has_errors() {
        let calendars: Vec<calendar::CalendarWithBinding> =
            calendar::list_user_calendars(&pool, user.id)
                .await
                .unwrap_or_default();

        let template = CalendarsTemplate {
            username: user.username,
            calendars,
            form,
            errors,
        };
        return Html(template.render().unwrap());
    }

    // Create the calendar
    let description = form.description.as_deref().filter(|s| !s.trim().is_empty());
    let binding_uri = Uuid::new_v4().to_string();

    match calendar::create_calendar(&pool, user.id, &binding_uri, &form.displayname, description)
        .await
    {
        Ok(_id) => {
            // Redirect to refresh the page
            Html(r#"<meta http-equiv="refresh" content="0; url=/" />"#.to_string())
        }
        Err(_) => {
            errors.add_general_error("Failed to create calendar. Please try again.");
            let calendars: Vec<calendar::CalendarWithBinding> =
                calendar::list_user_calendars(&pool, user.id)
                    .await
                    .unwrap_or_default();

            let template = CalendarsTemplate {
                username: user.username,
                calendars,
                form,
                errors,
            };
            Html(template.render().unwrap())
        }
    }
}

/// Handle calendar deletion
pub async fn handle_delete_calendar(
    State(pool): State<PgPool>,
    cookies: Cookies,
    Form(form): Form<DeleteCalendarForm>,
) -> Html<String> {
    let user = match require_auth(&pool, &cookies).await {
        Ok(u) => u,
        Err(redirect) => return redirect,
    };

    let calendar_id = match Uuid::parse_str(&form.calendar_id) {
        Ok(id) => id,
        Err(_) => return Html(r#"<meta http-equiv="refresh" content="0; url=/" />"#.to_string()),
    };

    // Delete the calendar (only if owner)
    match calendar::delete_calendar_if_owner(&pool, calendar_id, user.id).await {
        Ok(true) => Html(r#"<meta http-equiv="refresh" content="0; url=/" />"#.to_string()),
        _ => Html(r#"<meta http-equiv="refresh" content="0; url=/" />"#.to_string()),
    }
}

#[derive(Deserialize)]
pub struct DeleteCalendarForm {
    pub calendar_id: String,
}
