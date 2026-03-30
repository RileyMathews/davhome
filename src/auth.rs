use argon2::{
    Argon2,
    password_hash::{PasswordHash, PasswordHasher, PasswordVerifier, SaltString, rand_core::OsRng},
};
use sqlx::PgPool;
use tower_cookies::{Cookie, Cookies};
use uuid::Uuid;

use crate::models::user;

const SESSION_COOKIE_NAME: &str = "session";

pub fn hash_password(password: &str) -> Result<String, argon2::password_hash::Error> {
    let salt = SaltString::generate(&mut OsRng);
    let argon2 = Argon2::default();
    let password_hash = argon2.hash_password(password.as_bytes(), &salt)?;
    Ok(password_hash.to_string())
}

pub fn verify_password(password: &str, hash: &str) -> Result<bool, argon2::password_hash::Error> {
    let parsed_hash = PasswordHash::new(hash)?;
    let argon2 = Argon2::default();
    match argon2.verify_password(password.as_bytes(), &parsed_hash) {
        Ok(_) => Ok(true),
        Err(argon2::password_hash::Error::Password) => Ok(false),
        Err(e) => Err(e),
    }
}

pub fn set_session_cookie(cookies: &Cookies, user_id: Uuid) {
    let cookie = Cookie::new(SESSION_COOKIE_NAME, user_id.to_string());
    // In production, you'd want to set secure, http_only, same_site, etc.
    cookies.add(cookie);
}

pub fn clear_session_cookie(cookies: &Cookies) {
    cookies.remove(Cookie::new(SESSION_COOKIE_NAME, ""));
}

pub fn get_user_id_from_session(cookies: &Cookies) -> Option<Uuid> {
    cookies
        .get(SESSION_COOKIE_NAME)
        .and_then(|cookie| cookie.value().parse::<Uuid>().ok())
}

/// Requires a signed-in user, returning the user record
/// Returns a redirect response if not signed in
pub async fn require_auth(
    pool: &PgPool,
    cookies: &Cookies,
) -> Result<user::User, axum::response::Html<String>> {
    let user_id = get_user_id_from_session(cookies);

    match user_id {
        None => Err(axum::response::Html(
            r#"<meta http-equiv="refresh" content="0; url=/signin" />"#.to_string(),
        )),
        Some(uid) => match user::find_by_id(pool, uid).await {
            Ok(Some(user)) => Ok(user),
            _ => Err(axum::response::Html(
                r#"<meta http-equiv="refresh" content="0; url=/signin" />"#.to_string(),
            )),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hash_password_does_not_return_plaintext() {
        let password = "correct horse battery staple";
        let hash = hash_password(password).unwrap();

        assert_ne!(hash, password);
        assert!(hash.starts_with("$argon2"));
    }

    #[test]
    fn verify_password_accepts_matching_password() {
        let password = "correct horse battery staple";
        let hash = hash_password(password).unwrap();

        assert!(verify_password(password, &hash).unwrap());
    }

    #[test]
    fn verify_password_rejects_wrong_password() {
        let hash = hash_password("correct horse battery staple").unwrap();

        assert!(!verify_password("wrong password", &hash).unwrap());
    }
}
