"""Fixtures for tests that talk to a real FlashBlade.

They skip unless COSI_INTEGRATION_TESTS=1, so unit test runs and CI never
touch a FlashBlade. See "Integration tests" in the README.
"""

import os
import uuid

import pytest
from pypureclient.flashblade import BucketPatch

from cosi_driver.config import Config
from cosi_driver.flashblade import FlashBladeManager

REQUIRED_ENV = ("PUREFB_TARGET", "PUREFB_API_TOKEN")


def found(response) -> bool:
    """Whether a FlashBlade GET by name returned the object."""
    return response.status_code == 200 and bool(list(response.items))


@pytest.fixture
def fb():
    if os.getenv("COSI_INTEGRATION_TESTS") != "1":
        pytest.skip("COSI_INTEGRATION_TESTS is not 1")
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        pytest.fail(f"COSI_INTEGRATION_TESTS=1 but not set: {missing}")

    cfg = Config()
    return FlashBladeManager(
        target=cfg.fb_target,
        api_token=cfg.fb_api_token,
        s3_account=cfg.fb_s3_account,
        s3_endpoint=cfg.s3_endpoint,
        realm=cfg.fb_realm,
    )


class TestObjects:
    """Uniquely named objects for one test, removed even if the test fails."""

    __test__ = False

    def __init__(self, fb: FlashBladeManager):
        self.fb = fb
        self.name = f"cosi-it-{uuid.uuid4().hex[:12]}"
        self.user = f"{self.name}-u"
        self.keys: list[str] = []

    @property
    def full_bucket(self) -> str:
        return f"{self.fb.realm}::{self.name}" if self.fb.realm else self.name

    @property
    def full_user(self) -> str:
        return f"{self.fb._get_base_account_name()}/{self.user}"

    @property
    def full_policy(self) -> str:
        return f"{self.full_user}-policy"

    def full_key(self, key_id: str) -> str:
        return f"{self.fb.realm}::{key_id}" if self.fb.realm else key_id

    def remove(self):
        """Best effort: each call fails harmlessly if the object is gone."""
        client = self.fb._get_fresh_client()
        for key_id in self.keys:
            client.delete_object_store_access_keys(names=[self.full_key(key_id)])
        client.delete_object_store_access_policies_object_store_users(
            policy_names=[self.full_policy], member_names=[self.full_user]
        )
        client.delete_object_store_users(names=[self.full_user])
        client.delete_object_store_access_policies(names=[self.full_policy])
        client.patch_buckets(
            names=[self.full_bucket], bucket=BucketPatch(destroyed=True)
        )
        client.delete_buckets(names=[self.full_bucket])


@pytest.fixture
def objects(fb):
    test_objects = TestObjects(fb)
    yield test_objects
    test_objects.remove()
