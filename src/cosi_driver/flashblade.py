import logging
import json
from pypureclient import flashblade
from pypureclient.flashblade import BucketPost, BucketPatch, Reference, PolicyRuleObjectAccessPost, ObjectStoreAccessKeyPost

logger = logging.getLogger(__name__)


class FlashBladeManager:
    def __init__(self, target: str, api_token: str, s3_account: str, s3_endpoint: str, realm: str = None):
        self.s3_endpoint = s3_endpoint
        self.realm = realm
        self.s3_account = s3_account

        self.client = flashblade.Client(
            target=target,
            api_token=api_token,
            user_agent="OpenShift-COSI-Python-Driver/1.1"
        )

    def create_bucket(self, bucket_name: str) -> str:
        """Creates an S3 bucket on FlashBlade."""
        if self.realm:
            full_bucket_name = f"{self.realm}::{bucket_name}"
            full_account_name = f"{self.realm}::{self.s3_account}"
        else:
            full_bucket_name = bucket_name
            full_account_name = self.s3_account

        logger.info(f"Provisioning FlashBlade bucket: {bucket_name} under account {self.s3_account} and realm {self.realm}")

        bucket_req = BucketPost(account=Reference(name=full_account_name))

        response = self.client.post_buckets(
            names=[full_bucket_name],
            bucket=bucket_req
        )

        if response.status_code != 200:
            err = response.errors[0].message if response.errors else "Unknown error"
            raise RuntimeError(f"FlashBlade API error: {err}")

        return bucket_name

    def delete_bucket(self, bucket_name: str):
        """Destroys and eradicates a bucket from FlashBlade."""
        logger.info(f"Destroying FlashBlade bucket: {bucket_name}")

        if self.realm:
            full_bucket_name = f"{self.realm}::{bucket_name}"
        else:
            full_bucket_name = bucket_name
        # Mark bucket as destroyed (soft-delete)
        patch_req = BucketPatch(destroyed=True)
        patch_res = self.client.patch_buckets(names=[full_bucket_name], bucket=patch_req)

        if patch_res.status_code != 200:
            err = patch_res.errors[0].message if patch_res.errors else "Unknown error"
            raise RuntimeError(f"Failed to destroy bucket '{full_bucket_name}': {err}")

        # Permanently eradicate (hard-delete)
        response = self.client.delete_buckets(names=[full_bucket_name])

        if response.status_code != 200:
            err = response.errors[0].message if response.errors else "Unknown error"
            raise RuntimeError(f"FlashBlade API error eradicating bucket '{full_bucket_name}': {err}")

    def _get_base_account_name(self) -> str:
        """Helper to return the realm-prefixed account name if a realm is used."""
        if self.realm:
            return f"{self.realm}::{self.s3_account}"
        return self.s3_account

    def grant_access(self, user_name: str, bucket_name: str, access_mode: str = "readwrite") -> tuple[str, str]:
        """Creates an S3 user, native Purity access policy, attaches it, and generates keys."""

        base_account = self._get_base_account_name()
        full_user_name = f"{base_account}/{user_name}"
        policy_name = f"{base_account}/{user_name}-policy"

        logger.info(f"Creating S3 credentials for user: {full_user_name} on bucket {bucket_name} ({access_mode})")

        #Create S3 user
        user_res = self.client.post_object_store_users(names=[full_user_name])
        if user_res.status_code != 200:
            err = user_res.errors[0].message if user_res.errors else "Unknown error"
            raise RuntimeError(f"Failed to create S3 user: {err}")

        # Create empty IAM Policy

        pol_res = self.client.post_object_store_access_policies(names=[policy_name])
        if pol_res.status_code != 200:
            err = pol_res.errors[0].message if pol_res.errors else "Unknown error"
            raise RuntimeError(f"Failed to create policy: {err}")

        # Construct Policy Rules
        # Bucket access
        bucket_rule = PolicyRuleObjectAccessPost(
            effect="allow",
            actions=["s3:GetBucketLocation", "s3:ListBucket"],
            resources=[bucket_name]
        )

        # Object access
        if access_mode.lower() == "readwrite":
            object_actions = [
                "s3:AbortMultipartUpload",
                "s3:DeleteObject",
                "s3:GetObject",
                "s3:ListMultipartUploadParts",
                "s3:PutObject"
            ]
        else:
            object_actions = [
                "s3:GetObject",
                "s3:ListMultipartUploadParts"
            ]

        object_rule = PolicyRuleObjectAccessPost(
            effect="allow",
            actions=object_actions,
            resources=[f"{bucket_name}/*"]
        )

        # Post rules to the policy
        rules_to_create = [
            ("bucketaccess", bucket_rule),
            ("objectaccess", object_rule)
        ]

        for rule_name, rule_obj in rules_to_create:
            # Provide BOTH 'names' (for the rule) and 'policy_names' (for the parent)
            rule_res = self.client.post_object_store_access_policies_rules(
                names=[rule_name],
                policy_names=[policy_name],
                rule=rule_obj
            )

            if rule_res.status_code != 200:
                err = rule_res.errors[0].message if rule_res.errors else "Unknown error"
                raise RuntimeError(f"Failed to add rule '{rule_name}' to policy: {err}")

        # 5. Attach policy to user
        attach_res = self.client.post_object_store_access_policies_object_store_users(
            policy_names=[policy_name],
            member_names=[full_user_name]
        )
        if attach_res.status_code != 200:
            err = attach_res.errors[0].message if attach_res.errors else "Unknown error"
            raise RuntimeError(f"Failed to attach policy: {err}")

        # 6. Generate access key
        key_req = ObjectStoreAccessKeyPost(user=Reference(name=full_user_name))

        # Pass the payload to the required parameter
        key_res = self.client.post_object_store_access_keys(object_store_access_key=key_req)

        if key_res.status_code != 200:
            err = key_res.errors[0].message if key_res.errors else "Unknown error"
            raise RuntimeError(f"Failed to generate S3 keys: {err}")

        key_data = list(key_res.items)[0]
        return key_data.access_key_id, key_data.secret_access_key

    def revoke_access(self, access_key_name: str):
            """Revokes access key, detaches policy, and deletes both user and policy."""
            logger.info(f"Revoking FlashBlade S3 access key: {access_key_name}")

            # 1. Lookup the key to find the user
            if self.realm:
                full_access_key_name = f"{self.realm}::{access_key_name}"
            else:
                full_access_key_name = access_key_name

            key_lookup = self.client.get_object_store_access_keys(names=[full_access_key_name])

            # Convert ItemIterator to a list to check for existence and access the first item
            items_list = list(key_lookup.items)

            if key_lookup.status_code != 200 or not items_list:
                logger.warning(f"Access key {access_key_name} not found. Assuming already revoked.")
                return

            # Grab the first item
            full_user_name = items_list[0].user.name
            policy_name = f"{full_user_name}-policy"

            # 2. Delete Key
            self.client.delete_object_store_access_keys(names=[full_access_key_name])

            # 3. Detach Policy from User (prevents deletion conflict)
            self.client.delete_object_store_access_policies_object_store_users(
                policy_names=[policy_name],
                member_names=[full_user_name]
            )

            # 4. Delete User
            self.client.delete_object_store_users(names=[full_user_name])

            # 5. Delete Policy
            self.client.delete_object_store_access_policies(names=[policy_name])
