use argon2::{
    Argon2,
    password_hash::{PasswordHash, PasswordHasher, PasswordVerifier, SaltString, rand_core::OsRng},
};
use tower_cookies::{Cookie, Cookies};
use uuid::Uuid;

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
