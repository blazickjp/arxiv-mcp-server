# Use a Python base image with uv pre-installed
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS uv

# Set the working directory in the container
WORKDIR /app

# Enable bytecode compilation for better performance
ENV UV_COMPILE_BYTECODE=1

# Use copy mode for mounting cache to avoid linking issues
ENV UV_LINK_MODE=copy

# Install project dependencies using uv
RUN --mount=type=cache,target=/root/.cache/uv     --mount=type=bind,source=pyproject.toml,target=pyproject.toml     --mount=type=bind,source=uv.lock,target=uv.lock     uv sync --frozen --no-install-project --no-dev --no-editable

# Copy the application source (filtered by .dockerignore)
COPY . /app

# Install the application
RUN --mount=type=cache,target=/root/.cache/uv     uv sync --frozen --no-dev --no-editable

# Create a minimal Python environment
FROM python:3.11-slim-bookworm

# Set the working directory in the container
WORKDIR /app

# Copy the application and its virtual environment
COPY --from=uv /app /app

# Set the PATH to include the virtual environment
ENV PATH="/app/.venv/bin:$PATH"

# Set the default entrypoint
ENTRYPOINT ["python", "-m", "arxiv_mcp_server"]