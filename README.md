# COSI Driver for Flashblade

This is an UNOFFICIAL and NON SUPPORTED Driver. Use at your own risk.

The code works with release 0.2 so everything must use 0.2 APIs (v1alpha1).

https://github.com/kubernetes-sigs/container-object-storage-interface/tree/release-0.2

## How its built

Get cosi.proto from https://github.com/kubernetes-sigs/container-object-storage-interface/blob/main/proto/cosi.proto

And in a python environment with grpcio and grpcio-tools, run:

```
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. cosi.proto
```

This generates `cosi_pb2.py` (contains the data structures) and `cosi_pb2_grpc.py` (contains gRPC server stuff that we inherit from and implement)

## Quick test

Run the code like:

```
SOCKET_PATH="/tmp/cosi.sock" PYTHONPATH=src:src/cosi_driver uv run python -m cosi_driver.main
```

and from another terminal interact with the grpc server like:

```
grpcurl -plaintext \
  -import-path . \
  -proto cosi.proto \
  -d '{}' \
  unix:///tmp/cosi.sock cosi.v1alpha1.Identity/DriverGetInfo
```

## Integration tests

`tests/integration` runs the driver's bucket and access calls against a real FlashBlade, and checks FIPS behaviour on a FIPS-mode node. They skip unless `COSI_INTEGRATION_TESTS=1`, so CI does not run them.

They create uniquely named objects (`cosi-it-*`) in the configured account and realm, and remove them afterwards.

From anywhere that can reach the FlashBlade's management address:

```
COSI_INTEGRATION_TESTS=1 PUREFB_TARGET=... PUREFB_API_TOKEN=... PUREFB_REALM=... PUREFB_S3_ACCOUNT=... \
  uv run pytest tests/integration -v
```

To test an image on a cluster (needed for the FIPS tests), from the driver's namespace, which has the `pure-cosi-config` secret and the `storage-network` attachment. Set the image in `pod.yaml` first:

```
oc create configmap cosi-integration-tests --from-file=tests/integration/
oc apply -f tests/integration/pod.yaml
oc logs -f cosi-integration-tests
oc delete pod/cosi-integration-tests configmap/cosi-integration-tests
```

## Deploying it on OpenShift

### Deploy the COSI Controller, CRDs and COSI driver

* On your flashblade create a realm, a token scoped to it. If you are already using the CSI driver then the same realm and token can be used.
* Create (or reuse the NFS server) a server and put it on a subnet that's accessible for your openshift users.
* Create an object store account in this realm and export it with the server.
* In this repo go to `k8s` and modify the ExternalSecret.
* The driver deployment has an annotation that requires a network-attachment-definition be created. An example is provided but not included in the kustomization file: https://github.com/CCI-MOC/pure-cosi/blob/main/k8s/driver/resources/network-attachment-definition.yaml
* Deploy it with `oc apply -k .`. This will deploy the COSI CRDs, controllers and the driver.
