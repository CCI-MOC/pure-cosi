# COSI controller built with Red Hat's Go toolchain, so its crypto (TLS to the
# Kubernetes API) runs through RHEL's FIPS-validated OpenSSL. Upstream
# builds with CGO_ENABLED=0, which has no FIPS crypto.
FROM registry.access.redhat.com/ubi9/go-toolset:latest AS builder
ARG COSI_VERSION=v0.2.2
USER 0
RUN git clone --depth 1 --branch ${COSI_VERSION} \
      https://github.com/kubernetes-sigs/container-object-storage-interface /src
WORKDIR /src/controller
# -p and GOMAXPROCS cap the build on many-core nodes, where it otherwise
# hits the build pod's process limit.
ENV GOTOOLCHAIN=local GOFLAGS="-mod=vendor -p=4" GOMAXPROCS=4 \
    CGO_ENABLED=1 GOEXPERIMENT=strictfipsruntime
RUN go build -tags strictfipsruntime -o /out/controller ./cmd

FROM registry.access.redhat.com/ubi9/ubi-minimal:latest
LABEL org.opencontainers.image.title="COSI controller (FIPS build)" \
      org.opencontainers.image.source="https://github.com/kubernetes-sigs/container-object-storage-interface"
COPY --from=builder /out/controller /controller
USER 65532:65532
ENTRYPOINT ["/controller"]
