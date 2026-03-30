use askama::Template;
use axum::{
    extract::{Form, State},
    response::Html,
};
use serde::Deserialize;
use sqlx::PgPool;
use tower_cookies::Cookies;

use crate::auth::{clear_session_cookie, hash_password, set_session_cookie, verify_password};
use crate::forms::{FormErrors, validate_min_length, validate_required};
use crate::models::user;

#[derive(Deserialize, Default, Clone)]
pub struct SignupForm {
    pub username: String,
    pub password: String,
    pub confirm_password: String,
}

#[derive(Deserialize, Default, Clone)]
pub struct SigninForm {
    pub username: String,
    pub password: String,
}

#[derive(Template)]
#[template(path = "signup.html")]
pub struct SignupTemplate {
    pub form: SignupForm,
    pub errors: FormErrors,
}

#[derive(Template)]
#[template(path = "signin.html")]
pub struct SigninTemplate {
    pub form: SigninForm,
    pub errors: FormErrors,
}

pub async fn signup_page() -> Html<String> {
    let template = SignupTemplate {
        form: SignupForm::default(),
        errors: FormErrors::new(),
    };
    Html(template.render().unwrap())
}

pub async fn handle_signup(
    State(pool): State<PgPool>,
    cookies: Cookies,
    Form(form): Form<SignupForm>,
) -> Html<String> {
    let mut errors = FormErrors::new();

    // Validate required fields
    validate_required(&form.username, "username", &mut errors);
    validate_required(&form.password, "password", &mut errors);
    validate_required(&form.confirm_password, "confirm_password", &mut errors);

    // Validate minimum password length (16 characters as requested)
    if !form.password.is_empty() {
        validate_min_length(&form.password, "password", 16, &mut errors);
    }

    // Validate password match
    if !form.password.is_empty()
        && !form.confirm_password.is_empty()
        && form.password != form.confirm_password
    {
        errors.add_field_error("confirm_password", "Passwords do not match");
    }

    // If validation errors exist, return form with errors
    if errors.has_errors() {
        let template = SignupTemplate { form, errors };
        return Html(template.render().unwrap());
    }

    // Hash password
    let password_hash = match hash_password(&form.password) {
        Ok(hash) => hash,
        Err(_) => {
            errors.add_general_error("Error processing password. Please try again.");
            let template = SignupTemplate { form, errors };
            return Html(template.render().unwrap());
        }
    };

    // Create user
    match user::create_user(&pool, &form.username, &password_hash).await {
        Ok(user) => {
            set_session_cookie(&cookies, user.id);
            // Return a redirect response as HTML (askama templates return Html, but we need to redirect)
            Html(r#"<meta http-equiv="refresh" content="0; url=/" />"#.to_string())
        }
        Err(sqlx::Error::Database(db_err)) if db_err.is_unique_violation() => {
            errors.add_field_error("username", "Username already exists");
            let template = SignupTemplate { form, errors };
            Html(template.render().unwrap())
        }
        Err(_) => {
            errors.add_general_error("Error creating account. Please try again.");
            let template = SignupTemplate { form, errors };
            Html(template.render().unwrap())
        }
    }
}

pub async fn signin_page() -> Html<String> {
    let template = SigninTemplate {
        form: SigninForm::default(),
        errors: FormErrors::new(),
    };
    Html(template.render().unwrap())
}

pub async fn handle_signin(
    State(pool): State<PgPool>,
    cookies: Cookies,
    Form(form): Form<SigninForm>,
) -> Html<String> {
    let mut errors = FormErrors::new();

    // Validate required fields
    validate_required(&form.username, "username", &mut errors);
    validate_required(&form.password, "password", &mut errors);

    // If validation errors exist, return form with errors
    if errors.has_errors() {
        let template = SigninTemplate { form, errors };
        return Html(template.render().unwrap());
    }

    // Find user
    let user = match user::find_by_username(&pool, &form.username).await {
        Ok(user) => user,
        Err(_) => {
            errors.add_general_error("Error signing in. Please try again.");
            let template = SigninTemplate { form, errors };
            return Html(template.render().unwrap());
        }
    };

    match user {
        Some(user) => {
            match verify_password(&form.password, &user.password_hash) {
                Ok(true) => {
                    set_session_cookie(&cookies, user.id);
                    // Return a redirect response as HTML (askama templates return Html, but we need to redirect)
                    Html(r#"<meta http-equiv="refresh" content="0; url=/" />"#.to_string())
                }
                Ok(false) => {
                    errors.add_general_error("Invalid username or password");
                    let template = SigninTemplate { form, errors };
                    Html(template.render().unwrap())
                }
                Err(_) => {
                    errors.add_general_error("Error verifying credentials. Please try again.");
                    let template = SigninTemplate { form, errors };
                    Html(template.render().unwrap())
                }
            }
        }
        None => {
            errors.add_general_error("Invalid username or password");
            let template = SigninTemplate { form, errors };
            Html(template.render().unwrap())
        }
    }
}

pub async fn handle_signout(cookies: Cookies) -> Html<String> {
    clear_session_cookie(&cookies);
    // Return a redirect response as HTML
    Html(r#"<meta http-equiv="refresh" content="0; url=/" />"#.to_string())
}
