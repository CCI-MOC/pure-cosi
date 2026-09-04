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

## Deploying it on OpenShift

### Deploy the COSI Controller, CRDs and COSI driver

* On your flashblade create a realm, a token scoped to it. If you are already using the CSI driver then the same realm and token can be used.
* Create (or reuse the NFS server) a server and put it on a subnet that's accessible for your openshift users.
* Create an object store account in this realm and export it with the server.
* In this repo go to `k8s` and modify the ExternalSecret.
* The deployment has an annotation so that the driver can reach the management endpoint - so take care of that.
* Deploy it with `oc apply -k .`. This will deploy the COSI CRDs, controllers and the driver.
