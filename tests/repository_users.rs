use davhome::models::user;
use sqlx::PgPool;

mod common;

#[sqlx::test]
async fn repository_create_user_inserts_and_returns_user(pool: PgPool) -> sqlx::Result<()> {
    let created = user::create_user(&pool, "alice", "hash-1").await?;

    assert_eq!(created.username, "alice");
    assert_eq!(created.password_hash, "hash-1");

    let count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM users WHERE username = $1")
        .bind("alice")
        .fetch_one(&pool)
        .await?;
    assert_eq!(count, 1);

    Ok(())
}

#[sqlx::test]
async fn repository_find_by_username_returns_inserted_user(pool: PgPool) -> sqlx::Result<()> {
    let created = user::create_user(&pool, "bob", "hash-2").await?;

    let found = user::find_by_username(&pool, "bob").await?.unwrap();

    assert_eq!(found.id, created.id);
    assert_eq!(found.username, created.username);
    Ok(())
}

#[sqlx::test]
async fn repository_find_by_id_returns_inserted_user(pool: PgPool) -> sqlx::Result<()> {
    let created = user::create_user(&pool, "carol", "hash-3").await?;

    let found = user::find_by_id(&pool, created.id).await?.unwrap();

    assert_eq!(found.username, "carol");
    Ok(())
}

#[sqlx::test]
async fn repository_create_user_rejects_duplicate_username(pool: PgPool) -> sqlx::Result<()> {
    user::create_user(&pool, "dave", "hash-4").await?;

    let err = user::create_user(&pool, "dave", "hash-5")
        .await
        .unwrap_err();

    match err {
        sqlx::Error::Database(db_err) => assert!(db_err.is_unique_violation()),
        other => panic!("expected unique violation, got {other:?}"),
    }

    Ok(())
}
