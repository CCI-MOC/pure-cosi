import logging
import grpc

import cosi_pb2
import cosi_pb2_grpc
from cosi_driver.flashblade import FlashBladeManager

logger = logging.getLogger(__name__)


class IdentityServicer(cosi_pb2_grpc.IdentityServicer):
    def DriverGetInfo(self, request, context):
        """
        Identifies the driver to the COSI sidecar.
        The name must match the 'driverName' in your Kubernetes BucketClass.
        """
        return cosi_pb2.DriverGetInfoResponse(
            name="flashblade.cosi.purestorage.com"
        )


class ProvisionerServicer(cosi_pb2_grpc.ProvisionerServicer):
    def __init__(self, fb_manager: FlashBladeManager):
        self.fb = fb_manager

    def DriverCreateBucket(self, request, context):
        """
        Triggered when a BucketClaim is created.
        """
        try:
            # request.name is the generated name from the COSI sidecar
            bucket_id = self.fb.create_bucket(request.name)

            # COSI requires you to specify the protocol info (S3, Azure, or GCS)
            s3_info = cosi_pb2.S3(
                signature_version=cosi_pb2.S3SignatureVersion.S3V4
            )
            protocol_info = cosi_pb2.Protocol(s3=s3_info)

            return cosi_pb2.DriverCreateBucketResponse(
                bucket_id=bucket_id,
                bucket_info=protocol_info
            )

        except Exception as e:
            logger.error(f"DriverCreateBucket failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Failed to create bucket on FlashBlade: {str(e)}")
            return cosi_pb2.DriverCreateBucketResponse()

    def DriverDeleteBucket(self, request, context):
        """
        Triggered when a BucketClaim is deleted (if deletionPolicy is Delete).
        """
        try:
            self.fb.delete_bucket(request.bucket_id)
            return cosi_pb2.DriverDeleteBucketResponse()

        except Exception as e:
            logger.error(f"DriverDeleteBucket failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Failed to delete bucket on FlashBlade: {str(e)}")
            return cosi_pb2.DriverDeleteBucketResponse()

    def DriverGrantBucketAccess(self, request, context):
        """
        Triggered when a BucketAccess is created.
        """
        try:
            requested_mode = request.parameters.get("accessMode", "readwrite")
            access_key, secret_key = self.fb.grant_access(
                user_name=request.name,
                bucket_name=request.bucket_id,
                access_mode=requested_mode
            )

            # Package credentials into the 'secrets' map required by the COSI spec
            cred_details = cosi_pb2.CredentialDetails(
                secrets={
                    "accessKeyID": access_key,
                    "accessSecretKey": secret_key,
                    "endpoint": self.fb.s3_endpoint
                }
            )

            return cosi_pb2.DriverGrantBucketAccessResponse(
                account_id=access_key,
                credentials={"s3": cred_details}
            )

        except Exception as e:
            logger.error(f"DriverGrantBucketAccess failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Failed to grant access on FlashBlade: {str(e)}")
            return cosi_pb2.DriverGrantBucketAccessResponse()

    def DriverRevokeBucketAccess(self, request, context):
        """
        Triggered when a BucketAccess is deleted.
        """
        try:
            self.fb.revoke_access(request.account_id)
            return cosi_pb2.DriverRevokeBucketAccessResponse()

        except Exception as e:
            logger.error(f"DriverRevokeBucketAccess failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Failed to revoke access on FlashBlade: {str(e)}")
            return cosi_pb2.DriverRevokeBucketAccessResponse()
