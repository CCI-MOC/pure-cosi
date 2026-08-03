import logging
import os
import signal
import sys
from concurrent import futures

import grpc

import cosi_pb2_grpc
from cosi_driver.config import Config
from cosi_driver.flashblade import FlashBladeManager
from cosi_driver.servicers import IdentityServicer, ProvisionerServicer

# Configure structured logging for Kubernetes container logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def serve():
    cfg = Config()

    logger.info("Initializing Pure Storage FlashBlade Manager...")
    fb_manager = FlashBladeManager(
        target=cfg.fb_target,
        api_token=cfg.fb_api_token,
        s3_account=cfg.fb_s3_account,
        s3_endpoint=cfg.s3_endpoint,
        realm=cfg.fb_realm,
    )

    # Create the gRPC server with a thread pool
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    # Register our COSI servicers
    cosi_pb2_grpc.add_IdentityServicer_to_server(IdentityServicer(), server)
    cosi_pb2_grpc.add_ProvisionerServicer_to_server(
        ProvisionerServicer(fb_manager), server
    )

    socket_path = cfg.socket_path

    # Ensure socket directory exists
    os.makedirs(os.path.dirname(socket_path), exist_ok=True)

    # Clean up stale socket file if the container previously crashed
    if os.path.exists(socket_path):
        logger.warning(f"Removing pre-existing socket file at {socket_path}")
        os.remove(socket_path)

    # COSI sidecars communicate exclusively via UNIX sockets
    server.add_insecure_port(f"unix://{socket_path}")
    logger.info(f"COSI Driver starting and listening on unix://{socket_path}")

    server.start()

    # Handle graceful shutdown on OpenShift pod termination
    def handle_shutdown(signum, frame):
        logger.info(f"Received signal {signum}. Initiating graceful shutdown...")
        # Give active gRPC requests 5 seconds to finish before stopping
        server.stop(grace=5)
        # Clean up socket on exit
        if os.path.exists(socket_path):
            os.remove(socket_path)
        logger.info("Driver stopped cleanly.")
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    # Keep main thread alive
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
