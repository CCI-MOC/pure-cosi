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
    "get_buckets",
    "get_object_store_users",
    "get_object_store_access_policies",
    "get_object_store_access_policies_rules",
    "get_object_store_access_policies_object_store_users",
]


def _ok(mocker, items=None):
    resp = mocker.MagicMock()
    resp.status_code = 200
    resp.errors = []
    resp.items = items if items is not None else []
    return resp


def _err(mocker, message):
    resp = mocker.MagicMock()
    resp.status_code = 400
    resp.errors = [mocker.MagicMock(message=message)]
    return resp


def _named(mocker, name, **attrs):
    # MagicMock(name=...) names the mock itself, so set .name afterwards
    obj = mocker.MagicMock(**attrs)
    obj.name = name
    return obj


def _bucket(mocker, account, destroyed=False):
    return mocker.MagicMock(account=_named(mocker, account), destroyed=destroyed)


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
    mock_client.post_buckets.return_value = _err(mocker, "boom")
    mock_client.get_buckets.return_value = _err(mocker, "Bucket does not exist")

    with pytest.raises(RuntimeError, match="boom"):
        make_manager().create_bucket("my-bucket")


def test_create_bucket_retry_reuses_existing_bucket(make_manager, mock_client, mocker):
    mock_client.post_buckets.return_value = _err(mocker, "already exists")
    mock_client.get_buckets.return_value = _ok(
        mocker, items=[_bucket(mocker, "tenant-a::cosi-account")]
    )

    bucket_id = make_manager(realm="tenant-a").create_bucket("my-bucket")

    assert bucket_id == "my-bucket"
    mock_client.get_buckets.assert_called_once_with(names=["tenant-a::my-bucket"])


def test_create_bucket_existing_under_other_account_raises(
    make_manager, mock_client, mocker
):
    mock_client.post_buckets.return_value = _err(mocker, "already exists")
    mock_client.get_buckets.return_value = _ok(
        mocker, items=[_bucket(mocker, "someone-else")]
    )

    with pytest.raises(RuntimeError, match="someone-else"):
        make_manager().create_bucket("my-bucket")


def test_create_bucket_existing_destroyed_raises(make_manager, mock_client, mocker):
    mock_client.post_buckets.return_value = _err(mocker, "already exists")
    mock_client.get_buckets.return_value = _ok(
        mocker, items=[_bucket(mocker, "cosi-account", destroyed=True)]
    )

    with pytest.raises(RuntimeError, match="destroyed"):
        make_manager().create_bucket("my-bucket")


def test_delete_bucket_patch_then_delete(make_manager, mock_client):
    make_manager(realm="tenant-a").delete_bucket("my-bucket")

    patch_kwargs = mock_client.patch_buckets.call_args.kwargs
    assert patch_kwargs["names"] == ["tenant-a::my-bucket"]
    assert patch_kwargs["bucket"].destroyed is True
    mock_client.delete_buckets.assert_called_once_with(names=["tenant-a::my-bucket"])
    assert mock_client.patch_buckets.call_count == 1


def test_delete_bucket_already_eradicated_succeeds(make_manager, mock_client, mocker):
    mock_client.patch_buckets.return_value = _err(mocker, "Bucket does not exist")
    mock_client.get_buckets.return_value = _err(mocker, "Bucket does not exist")

    make_manager().delete_bucket("my-bucket")

    mock_client.delete_buckets.assert_not_called()


def test_delete_bucket_already_destroyed_is_eradicated(
    make_manager, mock_client, mocker
):
    mock_client.patch_buckets.return_value = _err(mocker, "already destroyed")
    mock_client.get_buckets.return_value = _ok(
        mocker, items=[_bucket(mocker, "cosi-account", destroyed=True)]
    )

    make_manager().delete_bucket("my-bucket")

    mock_client.delete_buckets.assert_called_once_with(names=["my-bucket"])


def test_delete_bucket_patch_failure_on_live_bucket_raises(
    make_manager, mock_client, mocker
):
    mock_client.patch_buckets.return_value = _err(mocker, "boom")
    mock_client.get_buckets.return_value = _ok(
        mocker, items=[_bucket(mocker, "cosi-account")]
    )

    with pytest.raises(RuntimeError, match="boom"):
        make_manager().delete_bucket("my-bucket")
    mock_client.delete_buckets.assert_not_called()


def test_delete_bucket_eradicate_failure_when_gone_succeeds(
    make_manager, mock_client, mocker
):
    mock_client.delete_buckets.return_value = _err(mocker, "boom")
    mock_client.get_buckets.return_value = _err(mocker, "Bucket does not exist")

    make_manager().delete_bucket("my-bucket")


def test_delete_bucket_eradicate_failure_raises(make_manager, mock_client, mocker):
    mock_client.delete_buckets.return_value = _err(mocker, "boom")
    mock_client.get_buckets.return_value = _ok(
        mocker, items=[_bucket(mocker, "cosi-account", destroyed=True)]
    )

    with pytest.raises(RuntimeError, match="boom"):
        make_manager().delete_bucket("my-bucket")


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


def test_grant_access_retry_reuses_existing_objects(make_manager, mock_client, mocker):
    # Every create fails because a previous attempt already made the object
    for method in [
        "post_object_store_users",
        "post_object_store_access_policies",
        "post_object_store_access_policies_rules",
        "post_object_store_access_policies_object_store_users",
    ]:
        getattr(mock_client, method).return_value = _err(mocker, "already exists")
    for method in [
        "get_object_store_users",
        "get_object_store_access_policies",
        "get_object_store_access_policies_rules",
        "get_object_store_access_policies_object_store_users",
    ]:
        getattr(mock_client, method).return_value = _ok(
            mocker, items=[mocker.MagicMock()]
        )
    key_item = mocker.MagicMock(access_key_id="AKIA456", secret_access_key="secret789")
    mock_client.post_object_store_access_keys.return_value.items = [key_item]

    access_key, secret_key = make_manager().grant_access("user-1", "my-bucket")

    assert (access_key, secret_key) == ("AKIA456", "secret789")
    assert mock_client.post_object_store_access_policies_rules.call_count == 2


def test_grant_access_create_failure_raises(make_manager, mock_client, mocker):
    mock_client.post_object_store_access_policies.return_value = _err(mocker, "boom")
    mock_client.get_object_store_access_policies.return_value = _err(
        mocker, "Policy does not exist"
    )

    with pytest.raises(RuntimeError, match="boom"):
        make_manager().grant_access("user-1", "my-bucket")
    mock_client.post_object_store_access_keys.assert_not_called()


@pytest.fixture
def existing_access(mocker, mock_client):
    """An access key AKIA123 on user cosi-account/user-1, which also has AKIA999."""
    key_item = mocker.MagicMock(user=_named(mocker, "cosi-account/user-1"))
    mock_client.get_object_store_access_keys.return_value = _ok(
        mocker, items=[key_item]
    )
    user = mocker.MagicMock(
        access_keys=[_named(mocker, "AKIA123"), _named(mocker, "AKIA999")]
    )
    mock_client.get_object_store_users.return_value = _ok(mocker, items=[user])


def test_revoke_access_deletes_policy_then_all_keys_then_user(
    make_manager, mock_client, existing_access
):
    make_manager().revoke_access("AKIA123")

    mock_client.delete_object_store_access_policies_object_store_users.assert_called_once_with(
        policy_names=["cosi-account/user-1-policy"],
        member_names=["cosi-account/user-1"],
    )
    mock_client.delete_object_store_access_policies.assert_called_once_with(
        names=["cosi-account/user-1-policy"]
    )
    deleted_keys = [
        c.kwargs["names"]
        for c in mock_client.delete_object_store_access_keys.call_args_list
    ]
    # The key from account_id goes last so a retry can still find the user
    assert deleted_keys == [["AKIA999"], ["AKIA123"]]
    mock_client.delete_object_store_users.assert_called_once_with(
        names=["cosi-account/user-1"]
    )


def test_revoke_access_realm_prefixes_extra_keys(
    make_manager, mock_client, existing_access
):
    make_manager(realm="tenant-a").revoke_access("AKIA123")

    deleted_keys = [
        c.kwargs["names"]
        for c in mock_client.delete_object_store_access_keys.call_args_list
    ]
    assert deleted_keys == [["tenant-a::AKIA999"], ["tenant-a::AKIA123"]]


def test_revoke_access_key_delete_failure_raises(
    make_manager, mock_client, existing_access, mocker
):
    mock_client.delete_object_store_access_keys.return_value = _err(mocker, "boom")

    with pytest.raises(RuntimeError, match="boom"):
        make_manager().revoke_access("AKIA123")
    mock_client.delete_object_store_users.assert_not_called()


def test_revoke_access_policy_delete_failure_raises(
    make_manager, mock_client, existing_access, mocker
):
    mock_client.delete_object_store_access_policies.return_value = _err(mocker, "boom")
    mock_client.get_object_store_access_policies.return_value = _ok(
        mocker, items=[mocker.MagicMock()]
    )

    with pytest.raises(RuntimeError, match="boom"):
        make_manager().revoke_access("AKIA123")
    mock_client.delete_object_store_access_keys.assert_not_called()


def test_revoke_access_retry_after_partial_cleanup_succeeds(
    make_manager, mock_client, existing_access, mocker
):
    # Policy and its attachment were removed by an earlier attempt
    mock_client.delete_object_store_access_policies_object_store_users.return_value = (
        _err(mocker, "Policy does not exist")
    )
    mock_client.delete_object_store_access_policies.return_value = _err(
        mocker, "Policy does not exist"
    )
    mock_client.get_object_store_access_policies_object_store_users.return_value = (
        _err(mocker, "Policy does not exist")
    )
    mock_client.get_object_store_access_policies.return_value = _err(
        mocker, "Policy does not exist"
    )

    make_manager().revoke_access("AKIA123")

    assert mock_client.delete_object_store_access_keys.call_count == 2


def test_revoke_access_user_delete_failure_is_logged_not_raised(
    make_manager, mock_client, existing_access, mocker
):
    mock_client.delete_object_store_users.return_value = _err(mocker, "boom")

    make_manager().revoke_access("AKIA123")

    assert mock_client.delete_object_store_access_keys.call_count == 2
