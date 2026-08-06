import logging
from pypureclient import flashblade
from pypureclient.flashblade import (
    BucketPost,
    BucketPatch,
    Reference,
    PolicyRuleObjectAccessPost,
    ObjectStoreAccessKeyPost,
)

logger = logging.getLogger(__name__)


class FlashBladeManager:
    def __init__(
        self,
        target: str,
        api_token: str,
        s3_account: str,
        s3_endpoint: str,
        realm: str = None,
    ):
        logger.debug(
            f"Initializing FlashBladeManager with target={target}, s3_account={s3_account}, realm={realm}"
        )
        self.target = target
        self.api_token = api_token
        self.s3_endpoint = s3_endpoint
        self.realm = realm
        self.s3_account = s3_account

    def _get_fresh_client(self):
        """Generates a new session token to prevent 403 expiration errors in long-running pods."""
        logger.debug("Authenticating new FlashBlade API session...")
        return flashblade.Client(
            target=self.target,
            api_token=self.api_token,
            user_agent="OpenShift-COSI-Python-Driver/1.1",
        )

    def create_bucket(self, bucket_name: str) -> str:
        """Creates an S3 bucket on FlashBlade."""
        client = self._get_fresh_client()

        if self.realm:
            full_bucket_name = f"{self.realm}::{bucket_name}"
            full_account_name = f"{self.realm}::{self.s3_account}"
        else:
            full_bucket_name = bucket_name
            full_account_name = self.s3_account

        logger.info(
            f"Provisioning FlashBlade bucket: {bucket_name} under account {self.s3_account} and realm {self.realm}"
        )
        logger.debug(
            f"Calculated full_bucket_name: '{full_bucket_name}', full_account_name: '{full_account_name}'"
        )

        bucket_req = BucketPost(account=Reference(name=full_account_name))

        logger.debug(f"Sending POST request to create bucket '{full_bucket_name}'...")
        response = client.post_buckets(names=[full_bucket_name], bucket=bucket_req)
        logger.debug(f"Bucket creation response status: {response.status_code}")

        if response.status_code != 200:
            err = response.errors[0].message if response.errors else "Unknown error"
            logger.debug(f"Bucket creation failed with error: {err}")
            raise RuntimeError(f"FlashBlade API error: {err}")

        logger.debug(f"Successfully provisioned bucket: {full_bucket_name}")
        return bucket_name

    def delete_bucket(self, bucket_name: str):
        """Destroys and eradicates a bucket from FlashBlade."""
        logger.info(f"Destroying FlashBlade bucket: {bucket_name}")
        client = self._get_fresh_client()
        if self.realm:
            full_bucket_name = f"{self.realm}::{bucket_name}"
        else:
            full_bucket_name = bucket_name

        logger.debug(f"Calculated full_bucket_name for deletion: '{full_bucket_name}'")

        # Mark bucket as destroyed (soft-delete)
        patch_req = BucketPatch(destroyed=True)
        logger.debug(
            f"Sending PATCH request to soft-delete bucket '{full_bucket_name}'..."
        )
        patch_res = client.patch_buckets(names=[full_bucket_name], bucket=patch_req)
        logger.debug(f"Bucket PATCH response status: {patch_res.status_code}")

        if patch_res.status_code != 200:
            err = patch_res.errors[0].message if patch_res.errors else "Unknown error"
            logger.debug(f"Bucket soft-delete failed: {err}")
            raise RuntimeError(f"Failed to destroy bucket '{full_bucket_name}': {err}")

        # Permanently eradicate (hard-delete)
        logger.debug(
            f"Sending DELETE request to eradicate bucket '{full_bucket_name}'..."
        )
        response = client.delete_buckets(names=[full_bucket_name])
        logger.debug(f"Bucket DELETE response status: {response.status_code}")

        if response.status_code != 200:
            err = response.errors[0].message if response.errors else "Unknown error"
            logger.debug(f"Bucket eradication failed: {err}")
            raise RuntimeError(
                f"FlashBlade API error eradicating bucket '{full_bucket_name}': {err}"
            )

        logger.debug(f"Successfully eradicated bucket: {full_bucket_name}")

    def _get_base_account_name(self) -> str:
        """Helper to return the realm-prefixed account name if a realm is used."""
        if self.realm:
            return f"{self.realm}::{self.s3_account}"
        return self.s3_account

    def grant_access(
        self, user_name: str, bucket_name: str, access_mode: str = "readwrite"
    ) -> tuple[str, str]:
        """Creates an S3 user, native Purity access policy, attaches it, and generates keys."""

        base_account = self._get_base_account_name()
        full_user_name = f"{base_account}/{user_name}"
        policy_name = f"{base_account}/{user_name}-policy"
        client = self._get_fresh_client()
        logger.info(
            f"Creating S3 credentials for user: {full_user_name} on bucket {bucket_name} ({access_mode})"
        )
        logger.debug(f"Policy name will be: '{policy_name}'")

        # Create S3 user
        logger.debug(f"Sending POST request to create S3 user '{full_user_name}'...")
        user_res = client.post_object_store_users(names=[full_user_name])
        logger.debug(f"User creation response status: {user_res.status_code}")

        if user_res.status_code != 200:
            err = user_res.errors[0].message if user_res.errors else "Unknown error"
            raise RuntimeError(f"Failed to create S3 user: {err}")

        # Create empty IAM Policy
        logger.debug(f"Sending POST request to create policy '{policy_name}'...")
        pol_res = client.post_object_store_access_policies(names=[policy_name])
        logger.debug(f"Policy creation response status: {pol_res.status_code}")

        if pol_res.status_code != 200:
            err = pol_res.errors[0].message if pol_res.errors else "Unknown error"
            raise RuntimeError(f"Failed to create policy: {err}")

        # Construct Policy Rules
        logger.debug("Constructing policy rules...")
        bucket_rule = PolicyRuleObjectAccessPost(
            effect="allow",
            actions=["s3:GetBucketLocation", "s3:ListBucket"],
            resources=[bucket_name],
        )
        normalized_mode = access_mode.lower().replace("-", "").replace("_", "").strip()
        if normalized_mode in ["readwrite", "rw"]:
            logger.debug("Setting readwrite access mode")
            object_actions = [
                "s3:AbortMultipartUpload",
                "s3:DeleteObject",
                "s3:GetObject",
                "s3:ListMultipartUploadParts",
                "s3:PutObject",
            ]
        else:
            logger.debug("Defaulting to read only access")
            object_actions = ["s3:GetObject", "s3:ListMultipartUploadParts"]
        logger.debug(f"Object actions determined as: {object_actions}")

        object_rule = PolicyRuleObjectAccessPost(
            effect="allow", actions=object_actions, resources=[f"{bucket_name}/*"]
        )

        rules_to_create = [("bucketaccess", bucket_rule), ("objectaccess", object_rule)]

        for rule_name, rule_obj in rules_to_create:
            logger.debug(
                f"Sending POST request to create rule '{rule_name}' on policy '{policy_name}'..."
            )
            rule_res = client.post_object_store_access_policies_rules(
                names=[rule_name], policy_names=[policy_name], rule=rule_obj
            )
            logger.debug(
                f"Rule '{rule_name}' creation response status: {rule_res.status_code}"
            )

            if rule_res.status_code != 200:
                err = rule_res.errors[0].message if rule_res.errors else "Unknown error"
                raise RuntimeError(f"Failed to add rule '{rule_name}' to policy: {err}")

        # 5. Attach policy to user
        logger.debug(
            f"Sending POST request to attach policy '{policy_name}' to user '{full_user_name}'..."
        )
        attach_res = client.post_object_store_access_policies_object_store_users(
            policy_names=[policy_name], member_names=[full_user_name]
        )
        logger.debug(f"Policy attachment response status: {attach_res.status_code}")

        if attach_res.status_code != 200:
            err = attach_res.errors[0].message if attach_res.errors else "Unknown error"
            raise RuntimeError(f"Failed to attach policy: {err}")

        # 6. Generate access key
        key_req = ObjectStoreAccessKeyPost(user=Reference(name=full_user_name))
        logger.debug(
            f"Sending POST request to generate access key for user '{full_user_name}'..."
        )
        key_res = client.post_object_store_access_keys(object_store_access_key=key_req)
        logger.debug(f"Key generation response status: {key_res.status_code}")

        if key_res.status_code != 200:
            err = key_res.errors[0].message if key_res.errors else "Unknown error"
            raise RuntimeError(f"Failed to generate S3 keys: {err}")

        key_data = list(key_res.items)[0]
        logger.debug("Successfully generated and retrieved access key.")
        return key_data.access_key_id, key_data.secret_access_key

    def revoke_access(self, access_key_name: str):
        """Revokes access key, detaches policy, and deletes both user and policy."""
        logger.info(f"Revoking FlashBlade S3 access key: {access_key_name}")
        client = self._get_fresh_client()
        # 1. Lookup the key to find the user
        if self.realm:
            full_access_key_name = f"{self.realm}::{access_key_name}"
        else:
            full_access_key_name = access_key_name

        logger.debug(
            f"Calculated full_access_key_name for revocation: '{full_access_key_name}'"
        )

        logger.debug(
            f"Sending GET request to lookup access key '{full_access_key_name}'..."
        )

        def _get_err_msg(response):
            if hasattr(response, "errors") and response.errors:
                return response.errors[0].message
            return "No error message provided by FlashBlade API"

        key_lookup = client.get_object_store_access_keys(names=[full_access_key_name])
        logger.debug(f"Key lookup response status: {key_lookup.status_code}")

        if key_lookup.status_code != 200:
            err_msg = _get_err_msg(key_lookup)

            # FlashBlade uses 400 (or 404) with "does not exist" when an access key isn't found
            if "does not exist" in err_msg.lower() or key_lookup.status_code == 404:
                logger.warning(
                    f"Access key {access_key_name} not found on FlashBlade. Status {key_lookup.status_code}: '{err_msg}'. "
                    "Assuming already revoked."
                )
                return

            logger.error(
                f"FlashBlade API error looking up access key '{full_access_key_name}' "
                f"(status {key_lookup.status_code}): {err_msg}"
            )

            raise RuntimeError(
                f"FlashBlade API error ({key_lookup.status_code}): {err_msg}"
            )

        # Convert ItemIterator to a list to check for existence and access the first item
        items_list = list(key_lookup.items)

        if not items_list:
            logger.warning(
                f"Access key {access_key_name} not found in items. Assuming already revoked."
            )
            return

        # Grab the first item
        full_user_name = items_list[0].user.name
        policy_name = f"{full_user_name}-policy"
        logger.debug(
            f"Found user: '{full_user_name}', associated policy: '{policy_name}'"
        )

        # 2. Delete Key
        logger.debug(
            f"Sending DELETE request for access key '{full_access_key_name}'..."
        )
        res_key = client.delete_object_store_access_keys(names=[full_access_key_name])
        logger.debug(f"Key deletion response status: {res_key.status_code}")

        # 3. Detach Policy from User (prevents deletion conflict)
        logger.debug(
            f"Sending DELETE request to detach policy '{policy_name}' from user '{full_user_name}'..."
        )
        res_detach = client.delete_object_store_access_policies_object_store_users(
            policy_names=[policy_name], member_names=[full_user_name]
        )
        logger.debug(f"Policy detach response status: {res_detach.status_code}")

        # 4. Delete User
        logger.debug(f"Sending DELETE request for user '{full_user_name}'...")
        res_user = client.delete_object_store_users(names=[full_user_name])
        logger.debug(f"User deletion response status: {res_user.status_code}")

        # 5. Delete Policy
        logger.debug(f"Sending DELETE request for policy '{policy_name}'...")
        res_policy = client.delete_object_store_access_policies(names=[policy_name])
        logger.debug(f"Policy deletion response status: {res_policy.status_code}")

        logger.debug(
            f"Successfully completed revocation workflow for: {access_key_name}"
        )
