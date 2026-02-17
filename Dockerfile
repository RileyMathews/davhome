FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.13 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-dev --no-install-project

COPY . /app/

ENV PATH="/app/.venv/bin:$PATH"

RUN chmod +x /app/startup.sh

EXPOSE 8000

CMD ["/app/startup.sh"]
