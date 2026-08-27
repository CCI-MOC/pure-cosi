import grpc
import pytest

import cosi_pb2
from cosi_driver.servicers import IdentityServicer, ProvisionerServicer


@pytest.fixture
def mock_fb(mocker):
    fb = mocker.MagicMock()
    fb.s3_endpoint = "https://s3.example.com"
    fb.create_bucket.return_value = "my-bucket"
    fb.grant_access.return_value = ("AKIA123", "secret456")
    return fb


@pytest.fixture
def grpc_context(mocker):
    return mocker.MagicMock()


@pytest.fixture
def provisioner(mock_fb):
    return ProvisionerServicer(mock_fb)


def test_driver_get_info_returns_driver_name():
    resp = IdentityServicer().DriverGetInfo(cosi_pb2.DriverGetInfoRequest(), None)
    assert resp.name == "flashblade.cosi.purestorage.com"


def test_create_bucket_success(provisioner, mock_fb, grpc_context):
    req = cosi_pb2.DriverCreateBucketRequest(name="my-bucket")
    resp = provisioner.DriverCreateBucket(req, grpc_context)

    mock_fb.create_bucket.assert_called_once_with("my-bucket")
    assert resp.bucket_id == "my-bucket"
    assert resp.bucket_info.s3.signature_version == cosi_pb2.S3SignatureVersion.S3V4
    grpc_context.set_code.assert_not_called()


def test_create_bucket_failure_sets_grpc_internal(provisioner, mock_fb, grpc_context):
    mock_fb.create_bucket.side_effect = RuntimeError("FlashBlade API error: boom")
    req = cosi_pb2.DriverCreateBucketRequest(name="my-bucket")

    resp = provisioner.DriverCreateBucket(req, grpc_context)

    grpc_context.set_code.assert_called_once_with(grpc.StatusCode.INTERNAL)
    details = grpc_context.set_details.call_args[0][0]
    assert "Failed to create bucket on FlashBlade" in details
    assert "boom" in details
    assert resp.bucket_id == ""


def test_delete_bucket_calls_backend(provisioner, mock_fb, grpc_context):
    req = cosi_pb2.DriverDeleteBucketRequest(bucket_id="my-bucket")
    provisioner.DriverDeleteBucket(req, grpc_context)

    mock_fb.delete_bucket.assert_called_once_with("my-bucket")
    grpc_context.set_code.assert_not_called()


def test_grant_access_default_readwrite(provisioner, mock_fb, grpc_context):
    req = cosi_pb2.DriverGrantBucketAccessRequest(name="user-1", bucket_id="my-bucket")
    resp = provisioner.DriverGrantBucketAccess(req, grpc_context)

    mock_fb.grant_access.assert_called_once_with(
        user_name="user-1", bucket_name="my-bucket", access_mode="readwrite"
    )
    assert resp.account_id == "AKIA123"
    secrets = dict(resp.credentials["s3"].secrets)
    assert secrets == {
        "accessKeyID": "AKIA123",
        "accessSecretKey": "secret456",
        "endpoint": "https://s3.example.com",
    }


def test_grant_access_readonly_from_parameters(provisioner, mock_fb, grpc_context):
    req = cosi_pb2.DriverGrantBucketAccessRequest(
        name="user-1",
        bucket_id="my-bucket",
        parameters={"accessMode": "readonly"},
    )
    provisioner.DriverGrantBucketAccess(req, grpc_context)

    mock_fb.grant_access.assert_called_once_with(
        user_name="user-1", bucket_name="my-bucket", access_mode="readonly"
    )


def test_revoke_access_uses_account_id(provisioner, mock_fb, grpc_context):
    req = cosi_pb2.DriverRevokeBucketAccessRequest(
        bucket_id="my-bucket", account_id="AKIA123"
    )
    provisioner.DriverRevokeBucketAccess(req, grpc_context)

    mock_fb.revoke_access.assert_called_once_with("AKIA123")
    grpc_context.set_code.assert_not_called()
