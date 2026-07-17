# One shared image for the API and both workers (SPEC.md §2), switched by
# CMD/args at deploy time (see infra/k8s/*.yaml) rather than three separate
# images. Tradeoff, noted here and in infra/k8s/README.md: a single image
# means one build/push and one set of base-layer CVEs to track, at the cost
# of coupling the API's and workers' deploy versions together -- acceptable
# for this system's size (three small, dependency-identical processes off
# one pyproject.toml). Split into per-service images if that coupling ever
# becomes a real deployment constraint.
#
# Multi-stage: the builder stage has a compiler toolchain for any dependency
# that needs one; the final stage ships only the installed venv and app code.

FROM python:3.12.8-slim-bookworm AS builder

WORKDIR /build

RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

COPY pyproject.toml ./
COPY src/ ./src/

# Runtime deps only -- no dev/test extras (pytest, ruff, mypy) in the image.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

FROM python:3.12.8-slim-bookworm AS final

RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
USER app

# No default command: each Deployment in infra/k8s/ sets its own
# command/args (uvicorn for the API, `python -m claims_pipeline.workers.*`
# for the workers) -- see infra/k8s/README.md.
CMD ["python", "--version"]
