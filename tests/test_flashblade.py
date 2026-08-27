import pytest

from cosi_driver.flashblade import FlashBladeManager

CLIENT_METHODS = [
    "post_buckets",
    "patch_buckets",
    "delete_buckets",
    "post_object_store_users",
    "post_object_store_access_policies",
    "post_object_store_access_policies_rules",
    "post_object_store_access_policies_object_store_users",
    "post_object_store_access_keys",
    "get_object_store_access_keys",
    "delete_object_store_access_keys",
    "delete_object_store_access_policies_object_store_users",
    "delete_object_store_users",
    "delete_object_store_access_policies",
]


def _ok(mocker, items=None):
    resp = mocker.MagicMock()
    resp.status_code = 200
    resp.errors = []
    resp.items = items if items is not None else []
    return resp


@pytest.fixture
def mock_client(mocker):
    client = mocker.MagicMock()
    for method in CLIENT_METHODS:
        getattr(client, method).return_value = _ok(mocker)
    return client


@pytest.fixture
def make_manager(mocker, mock_client):
    mocker.patch.object(
        FlashBladeManager, "_get_fresh_client", return_value=mock_client
    )

    def _make(realm=None):
        return FlashBladeManager(
            target="10.0.0.1",
            api_token="token",
            s3_account="cosi-account",
            s3_endpoint="https://s3.example.com",
            realm=realm,
        )

    return _make


def _object_rule(mock_client):
    return mock_client.post_object_store_access_policies_rules.call_args_list[1].kwargs[
        "rule"
    ]


def test_create_bucket_without_realm(make_manager, mock_client):
    bucket_id = make_manager().create_bucket("my-bucket")

    assert bucket_id == "my-bucket"
    kwargs = mock_client.post_buckets.call_args.kwargs
    assert kwargs["names"] == ["my-bucket"]
    assert kwargs["bucket"].account.name == "cosi-account"


def test_create_bucket_with_realm_prefix(make_manager, mock_client):
    make_manager(realm="tenant-a").create_bucket("my-bucket")

    kwargs = mock_client.post_buckets.call_args.kwargs
    assert kwargs["names"] == ["tenant-a::my-bucket"]
    assert kwargs["bucket"].account.name == "tenant-a::cosi-account"


def test_create_bucket_api_error_raises(make_manager, mock_client, mocker):
    mock_client.post_buckets.return_value.status_code = 400
    mock_client.post_buckets.return_value.errors = [
        mocker.MagicMock(message="already exists")
    ]

    with pytest.raises(RuntimeError, match="already exists"):
        make_manager().create_bucket("my-bucket")


def test_delete_bucket_patch_then_delete(make_manager, mock_client):
    make_manager(realm="tenant-a").delete_bucket("my-bucket")

    patch_kwargs = mock_client.patch_buckets.call_args.kwargs
    assert patch_kwargs["names"] == ["tenant-a::my-bucket"]
    assert patch_kwargs["bucket"].destroyed is True
    mock_client.delete_buckets.assert_called_once_with(names=["tenant-a::my-bucket"])
    assert mock_client.patch_buckets.call_count == 1


def test_grant_access_readwrite_policy_actions(make_manager, mock_client, mocker):
    key_item = mocker.MagicMock(access_key_id="AKIA123", secret_access_key="secret456")
    mock_client.post_object_store_access_keys.return_value.items = [key_item]

    access_key, secret_key = make_manager().grant_access(
        "user-1", "my-bucket", "readwrite"
    )

    assert (access_key, secret_key) == ("AKIA123", "secret456")
    mock_client.post_object_store_users.assert_called_once_with(
        names=["cosi-account/user-1"]
    )
    object_actions = _object_rule(mock_client).actions
    assert "s3:PutObject" in object_actions
    assert "s3:DeleteObject" in object_actions
    assert "s3:GetObject" in object_actions


def test_grant_access_readonly_policy_actions(make_manager, mock_client, mocker):
    key_item = mocker.MagicMock(access_key_id="AKIA123", secret_access_key="secret456")
    mock_client.post_object_store_access_keys.return_value.items = [key_item]

    make_manager().grant_access("user-1", "my-bucket", "READ_ONLY")

    object_actions = _object_rule(mock_client).actions
    assert object_actions == ["s3:GetObject", "s3:ListMultipartUploadParts"]
    assert "s3:PutObject" not in object_actions


def test_revoke_access_key_not_found_is_idempotent(make_manager, mock_client, mocker):
    mock_client.get_object_store_access_keys.return_value.status_code = 400
    mock_client.get_object_store_access_keys.return_value.errors = [
        mocker.MagicMock(message="Access key does not exist")
    ]

    make_manager().revoke_access("AKIA123")

    mock_client.delete_object_store_access_keys.assert_not_called()
    mock_client.delete_object_store_users.assert_not_called()
