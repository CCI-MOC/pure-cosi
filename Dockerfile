# Red Hat's UBI Python, so TLS to the FlashBlade goes through RHEL's
# OpenSSL: on a FIPS-mode node it runs the FIPS provider. Debian's OpenSSL
# has no FIPS provider, which made this image fail FIPS image scans.
FROM registry.access.redhat.com/ubi9/python-314:9.8 AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

# Build the venv against the image's own Python, never a downloaded one,
# so the runner stage has the same interpreter at the same path.
ENV UV_LINK_MODE=copy \
    UV_PYTHON=/usr/bin/python3.14 \
    UV_PYTHON_DOWNLOADS=never

USER 0
WORKDIR /app

# cryptography's PyPI wheels carry their own OpenSSL, without FIPS. Build it
# from source against RHEL's OpenSSL instead (py-pure-client loads it).
RUN dnf install -y --setopt=install_weak_deps=0 rust cargo openssl-devel \
    && dnf clean all
ENV UV_NO_BINARY_PACKAGE=cryptography

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM registry.access.redhat.com/ubi9/python-314-minimal:9.8 AS runner

USER 0
WORKDIR /app

# Copy virtual environment and app code
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

# Set environment variables
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src:/app/src/cosi_driver" \
    COSI_SOCKET_PATH="/var/lib/cosi/cosi.sock"

RUN mkdir -p /var/lib/cosi && \
    chown -R 1001:0 /var/lib/cosi && \
    chmod -R g+w /var/lib/cosi

USER 1001

ENTRYPOINT ["python", "-m", "cosi_driver.main"]
