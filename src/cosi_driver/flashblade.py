# pypureclient exposes model classes via a lazy module __getattr__, which the
# type checker cannot resolve (it misinfers them as str). Suppress call checks
# for this file; the SDK is untyped and these are false positives.
# pyright: reportCallIssue=false
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


def _err_msg(response) -> str:
    if getattr(response, "errors", None):
        return response.errors[0].message
    return "Unknown error"


def _items(response) -> list:
    """Returns the items of a GET response, or [] if the object does not exist."""
    if response.status_code == 200:
        return list(response.items)
    if "does not exist" in _err_msg(response).lower():
        return []
    raise RuntimeError(
        f"FlashBlade API error ({response.status_code}): {_err_msg(response)}"
    )


def _ensure_created(post_res, what: str, lookup):
    """Treats a failed create as success if the object already exists.

    The COSI sidecar retries a request with the same names after any failure,
    so objects left over from a previous attempt must be reused.
    """
    if post_res.status_code == 200:
        return
    if _items(lookup()):
        logger.info(f"{what} already exists, reusing it")
        return
    raise RuntimeError(f"Failed to create {what}: {_err_msg(post_res)}")


def _ensure_deleted(delete_res, what: str, lookup):
    """Treats a failed delete as success if the object is already gone."""
    if delete_res.status_code == 200:
        return
    if not _items(lookup()):
        logger.info(f"{what} already gone")
        return
    raise RuntimeError(f"Failed to delete {what}: {_err_msg(delete_res)}")


class FlashBladeManager:
    def __init__(
        self,
        target: str,
        api_token: str,
        s3_account: str,
        s3_endpoint: str,
        realm: str | None = None,
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
            err = _err_msg(response)
            logger.debug(f"Bucket creation failed with error: {err}")
            # A retried request finds the bucket from the earlier attempt
            existing = _items(client.get_buckets(names=[full_bucket_name]))
            if not existing:
                raise RuntimeError(f"FlashBlade API error: {err}")
            bucket = existing[0]
            if bucket.destroyed:
                raise RuntimeError(
                    f"Bucket '{full_bucket_name}' exists but is destroyed: {err}"
                )
            if bucket.account.name != full_account_name:
                raise RuntimeError(
                    f"Bucket '{full_bucket_name}' already exists under account "
                    f"'{bucket.account.name}', not '{full_account_name}'"
                )
            logger.info(f"Bucket {full_bucket_name} already exists, reusing it")

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
            err = _err_msg(patch_res)
            logger.debug(f"Bucket soft-delete failed: {err}")
            # A retried request may find the bucket already destroyed or eradicated
            existing = _items(client.get_buckets(names=[full_bucket_name]))
            if not existing:
                logger.info(f"Bucket {full_bucket_name} already eradicated")
                return
            if not existing[0].destroyed:
                raise RuntimeError(
                    f"Failed to destroy bucket '{full_bucket_name}': {err}"
                )

        # Permanently eradicate (hard-delete)
        logger.debug(
            f"Sending DELETE request to eradicate bucket '{full_bucket_name}'..."
        )
        response = client.delete_buckets(names=[full_bucket_name])
        logger.debug(f"Bucket DELETE response status: {response.status_code}")

        _ensure_deleted(
            response,
            f"bucket '{full_bucket_name}'",
            lambda: client.get_buckets(names=[full_bucket_name]),
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
        _ensure_created(
            user_res,
            f"S3 user '{full_user_name}'",
            lambda: client.get_object_store_users(names=[full_user_name]),
        )

        # Create empty IAM Policy
        logger.debug(f"Sending POST request to create policy '{policy_name}'...")
        pol_res = client.post_object_store_access_policies(names=[policy_name])
        logger.debug(f"Policy creation response status: {pol_res.status_code}")
        _ensure_created(
            pol_res,
            f"policy '{policy_name}'",
            lambda: client.get_object_store_access_policies(names=[policy_name]),
        )

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
            _ensure_created(
                rule_res,
                f"rule '{rule_name}' on policy '{policy_name}'",
                lambda: client.get_object_store_access_policies_rules(
                    names=[rule_name], policy_names=[policy_name]
                ),
            )

        # 5. Attach policy to user
        logger.debug(
            f"Sending POST request to attach policy '{policy_name}' to user '{full_user_name}'..."
        )
        attach_res = client.post_object_store_access_policies_object_store_users(
            policy_names=[policy_name], member_names=[full_user_name]
        )
        logger.debug(f"Policy attachment response status: {attach_res.status_code}")
        _ensure_created(
            attach_res,
            f"attachment of policy '{policy_name}' to '{full_user_name}'",
            lambda: client.get_object_store_access_policies_object_store_users(
                policy_names=[policy_name], member_names=[full_user_name]
            ),
        )

        # 6. Generate access key. On a retry the user may already have a key,
        # but its secret can't be read back, so always mint a new one. Keys
        # from earlier attempts are left alone (one may already be in the
        # sidecar's Secret, which it never overwrites); revoke_access removes
        # every key on the user.
        key_req = ObjectStoreAccessKeyPost(user=Reference(name=full_user_name))
        logger.debug(
            f"Sending POST request to generate access key for user '{full_user_name}'..."
        )
        key_res = client.post_object_store_access_keys(object_store_access_key=key_req)
        logger.debug(f"Key generation response status: {key_res.status_code}")

        if key_res.status_code != 200:
            raise RuntimeError(f"Failed to generate S3 keys: {_err_msg(key_res)}")

        key_data = list(key_res.items)[0]
        logger.debug("Successfully generated and retrieved access key.")
        return key_data.access_key_id, key_data.secret_access_key

    def _full_key_name(self, access_key_name: str) -> str:
        """Access keys must be realm-prefixed when looking them up or deleting them."""
        if self.realm and not access_key_name.startswith(f"{self.realm}::"):
            return f"{self.realm}::{access_key_name}"
        return access_key_name

    def revoke_access(self, access_key_name: str):
        """Revokes access key, detaches policy, and deletes both user and policy."""
        logger.info(f"Revoking FlashBlade S3 access key: {access_key_name}")
        client = self._get_fresh_client()
        # 1. Lookup the key to find the user
        full_access_key_name = self._full_key_name(access_key_name)

        logger.debug(
            f"Calculated full_access_key_name for revocation: '{full_access_key_name}'"
        )

        logger.debug(
            f"Sending GET request to lookup access key '{full_access_key_name}'..."
        )

        key_lookup = client.get_object_store_access_keys(names=[full_access_key_name])
        logger.debug(f"Key lookup response status: {key_lookup.status_code}")

        if key_lookup.status_code != 200:
            err_msg = _err_msg(key_lookup)

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

        # The steps below are ordered so a retry can always finish the job:
        # everything up to and including the key deletion raises on failure,
        # and the key named in account_id is deleted last because a retry
        # finds the user through it.

        # 2. Detach Policy from User (prevents deletion conflict)
        logger.debug(
            f"Sending DELETE request to detach policy '{policy_name}' from user '{full_user_name}'..."
        )
        res_detach = client.delete_object_store_access_policies_object_store_users(
            policy_names=[policy_name], member_names=[full_user_name]
        )
        logger.debug(f"Policy detach response status: {res_detach.status_code}")
        _ensure_deleted(
            res_detach,
            f"attachment of policy '{policy_name}' to '{full_user_name}'",
            lambda: client.get_object_store_access_policies_object_store_users(
                policy_names=[policy_name], member_names=[full_user_name]
            ),
        )

        # 3. Delete Policy
        logger.debug(f"Sending DELETE request for policy '{policy_name}'...")
        res_policy = client.delete_object_store_access_policies(names=[policy_name])
        logger.debug(f"Policy deletion response status: {res_policy.status_code}")
        _ensure_deleted(
            res_policy,
            f"policy '{policy_name}'",
            lambda: client.get_object_store_access_policies(names=[policy_name]),
        )

        # 4. Delete Keys. A retried grant can leave more than one key on the
        # user, and any of them may be the one in the workload's Secret.
        users = _items(client.get_object_store_users(names=[full_user_name]))
        key_names = [
            self._full_key_name(ref.name)
            for user in users
            for ref in (user.access_keys or [])
        ]
        other_keys = [k for k in key_names if k != full_access_key_name]
        for key_name in other_keys + [full_access_key_name]:
            logger.debug(f"Sending DELETE request for access key '{key_name}'...")
            res_key = client.delete_object_store_access_keys(names=[key_name])
            logger.debug(f"Key deletion response status: {res_key.status_code}")
            _ensure_deleted(
                res_key,
                f"access key '{key_name}'",
                lambda: client.get_object_store_access_keys(names=[key_name]),
            )

        # 5. Delete User. A retry can't get back here once the key is gone, so
        # log instead of raising; the user has no keys or policy left.
        logger.debug(f"Sending DELETE request for user '{full_user_name}'...")
        res_user = client.delete_object_store_users(names=[full_user_name])
        logger.debug(f"User deletion response status: {res_user.status_code}")
        try:
            _ensure_deleted(
                res_user,
                f"S3 user '{full_user_name}'",
                lambda: client.get_object_store_users(names=[full_user_name]),
            )
        except RuntimeError as e:
            logger.error(f"{e}. The user has no access left; delete it manually.")

        logger.debug(
            f"Successfully completed revocation workflow for: {access_key_name}"
        )
