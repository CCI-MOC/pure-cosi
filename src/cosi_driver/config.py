import os


class Config:
    def __init__(self):
        self.debug = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

        # FlashBlade Management IP/FQDN and API Token
        self.fb_target = os.getenv("PUREFB_TARGET", "10.3.11.50")
        self.fb_api_token = os.getenv("PUREFB_API_TOKEN", "")

        # Multi-tenancy boundaries
        # Realm is optional
        self.fb_realm = os.getenv("PUREFB_REALM", None)

        # Default FlashBlade S3 account under which COSI creates buckets
        self.fb_s3_account = os.getenv("PUREFB_S3_ACCOUNT", "cosi-account")

        # Public S3 endpoint returned to OpenShift workloads for S3 access
        self.s3_endpoint = os.getenv(
            "S3_ENDPOINT", "https://s3.flashblade.example.com"
        )

        # UNIX Socket path required for gRPC communication with the COSI sidecar
        self.socket_path = os.getenv(
            "COSI_SOCKET_PATH", "/var/lib/cosi/cosi.sock"
        )
