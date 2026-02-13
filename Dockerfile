FROM python:3.14-slim

WORKDIR /app

# Pin uv version for reproducible builds
COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /usr/local/bin/uv

# Copy dependency files first (changes rarely → cached layer)
COPY pyproject.toml uv.lock ./

# Install dependencies into .venv without the project itself
RUN uv sync --frozen --no-dev --no-install-project

# Copy source code, config, and README (hatchling requires readme)
COPY README.md ./
COPY src/ src/
COPY config/ config/

# Install the project (fast — deps already cached above)
RUN uv sync --frozen --no-dev --no-editable

EXPOSE 8000

# Run uvicorn directly from the venv (no uv overhead at runtime)
CMD [".venv/bin/uvicorn", "sjifire.mcp.server:app", "--host", "0.0.0.0", "--port", "8000"]
