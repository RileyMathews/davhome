use askama::Template;
use axum::{response::Html, routing::get, Router};
use tower_http::trace::{self, TraceLayer};
use tracing::Level;
use tracing_subscriber::fmt::format::FmtSpan;

#[derive(Template)]
#[template(path = "index.html")]
struct IndexTemplate;

async fn index() -> Html<String> {
    let template = IndexTemplate;
    Html(template.render().unwrap())
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .json()
        .with_span_events(FmtSpan::CLOSE)
        .init();

    let app = Router::new()
        .route("/", get(index))
        .layer(
            TraceLayer::new_for_http()
                .make_span_with(trace::DefaultMakeSpan::new().level(Level::INFO))
                .on_response(trace::DefaultOnResponse::new().level(Level::INFO)),
        );

    let listener = tokio::net::TcpListener::bind("0.0.0.0:3000").await.unwrap();
    axum::serve(listener, app).await.unwrap();
}