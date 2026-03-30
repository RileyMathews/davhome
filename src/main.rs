use davhome::{build_app, db};
use std::net::SocketAddr;
use tracing_subscriber::fmt::format::FmtSpan;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    dotenvy::dotenv().ok();

    tracing_subscriber::fmt()
        .json()
        .with_span_events(FmtSpan::CLOSE)
        .init();

    let database_url = std::env::var("DATABASE_URL")
        .unwrap_or_else(|_| "postgres://davhome:davhome@localhost:5432/davhome".to_string());

    let pool = sqlx::postgres::PgPoolOptions::new()
        .max_connections(5)
        .connect(&database_url)
        .await?;

    db::run_migrations(&pool).await?;
    tracing::info!("Database migrations completed successfully");

    let app = build_app(pool);

    let addr = SocketAddr::from(([0, 0, 0, 0], 3000));
    let listener = tokio::net::TcpListener::bind(addr).await?;
    tracing::info!("Server running on http://{}", addr);

    axum::serve(listener, app).await?;

    Ok(())
}
