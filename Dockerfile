FROM python:3.14-slim

WORKDIR /app

# Pin uv version for reproducible builds
COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /usr/local/bin/uv

# Copy dependency files first (changes rarely → cached layer)
COPY pyproject.toml uv.lock ./

# Install dependencies into .venv without the project itself
RUN uv sync --frozen --no-dev --no-install-project

# Copy source code, config, docs, and README (hatchling requires readme)
COPY README.md ./
COPY src/ src/
COPY config/ config/
COPY docs/ docs/

# Install the project (fast — deps already cached above)
RUN uv sync --frozen --no-dev --no-editable

EXPOSE 8000

# Use the installed entry point (goes through main() for logging setup)
CMD [".venv/bin/mcp-server"]
