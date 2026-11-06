"""The driver's FlashBlade calls, checked against a real FlashBlade.

Each step is confirmed by reading the FlashBlade back, not by trusting the
driver's return value: revoke_access does not check the status of its deletes.
"""

from conftest import found


def _rule_actions(client, policy: str, rule: str) -> list[str]:
    response = client.get_object_store_access_policies_rules(
        policy_names=[policy], names=[rule]
    )
    assert response.status_code == 200
    # response.items can only be read once.
    items = list(response.items)
    assert items
    return list(items[0].actions)


def test_create_and_delete_bucket(fb, objects):
    client = fb._get_fresh_client()

    assert fb.create_bucket(objects.name) == objects.name
    assert found(client.get_buckets(names=[objects.full_bucket]))

    fb.delete_bucket(objects.name)
    assert not found(client.get_buckets(names=[objects.full_bucket]))


def test_grant_and_revoke_access(fb, objects):
    client = fb._get_fresh_client()
    fb.create_bucket(objects.name)

    key_id, secret = fb.grant_access(objects.user, objects.name)
    objects.keys.append(key_id)
    assert key_id and secret
    key = objects.full_key(key_id)
    assert found(client.get_object_store_users(names=[objects.full_user]))
    assert found(
        client.get_object_store_access_policies(names=[objects.full_policy])
    )
    assert found(client.get_object_store_access_keys(names=[key]))

    fb.revoke_access(key_id)
    assert not found(client.get_object_store_access_keys(names=[key]))
    assert not found(client.get_object_store_users(names=[objects.full_user]))
    assert not found(
        client.get_object_store_access_policies(names=[objects.full_policy])
    )

    # The sidecar can retry a revoke; a second one must not raise.
    fb.revoke_access(key_id)


def test_read_only_access_has_no_write_rules(fb, objects):
    client = fb._get_fresh_client()
    fb.create_bucket(objects.name)

    key_id, _ = fb.grant_access(objects.user, objects.name, "readonly")
    objects.keys.append(key_id)

    actions = _rule_actions(client, objects.full_policy, "objectaccess")
    assert "s3:GetObject" in actions
    assert "s3:PutObject" not in actions
    assert "s3:DeleteObject" not in actions
