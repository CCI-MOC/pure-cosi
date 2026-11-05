# FIPS builds of the upstream COSI controller and sidecar

The driver here is one of three COSI parts. The other two, the controller
and the sidecar, come from
[kubernetes-sigs/container-object-storage-interface](https://github.com/kubernetes-sigs/container-object-storage-interface),
whose images are built with `CGO_ENABLED=0` and have no FIPS crypto. Both talk
TLS to the Kubernetes API, and the sidecar writes each bucket's S3 keys to a
Secret.

These Dockerfiles build the same upstream release (`COSI_VERSION`, default
`v0.2.2`) with Red Hat's Go toolchain (`ubi9/go-toolset`, `CGO_ENABLED=1`,
`strictfipsruntime`) on `ubi9-minimal`, so their crypto runs through RHEL's
FIPS-validated OpenSSL, as OpenShift's own components do. The source is not
changed.

CI publishes them as `ghcr.io/cci-moc/pure-cosi/cosi-controller` and
`ghcr.io/cci-moc/pure-cosi/cosi-sidecar`.

Checked 2026-11-05 on a FIPS-mode cluster: Red Hat's `check-payload` passes
both images (upstream's fail it: "go binary is not CGO_ENABLED"), and both
binaries start. Not yet tested together with the driver against a
FlashBlade.
