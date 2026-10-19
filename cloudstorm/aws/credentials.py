"""
AWS credentials and session management.
"""
import threading

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from ..utils.display import print_error

_sessions = {}  # Cache sessions by profile
_sessions_lock = threading.Lock()


def get_session(profile: str | None = None, region: str | None = None) -> boto3.Session:
    """
    Get a boto3 session.

    Args:
        profile: AWS profile name (uses default if None).
        region: AWS region (uses session default if None).

    Returns:
        boto3.Session instance.
    """
    cache_key = f"{profile}:{region}"

    with _sessions_lock:
        if cache_key not in _sessions:
            kwargs = {}
            if profile:
                kwargs["profile_name"] = profile
            if region:
                kwargs["region_name"] = region

            _sessions[cache_key] = boto3.Session(**kwargs)

        return _sessions[cache_key]


def get_client(service: str, region: str, profile: str | None = None):
    """
    Get a boto3 client for a service.

    Args:
        service: AWS service name (e.g., "ec2", "ssm").
        region: AWS region.
        profile: AWS profile name.

    Returns:
        boto3 client.
    """
    session = get_session(profile, region)
    return session.client(service, region_name=region)


def get_resource(service: str, region: str, profile: str | None = None):
    """
    Get a boto3 resource for a service.

    Args:
        service: AWS service name.
        region: AWS region.
        profile: AWS profile name.

    Returns:
        boto3 resource.
    """
    session = get_session(profile, region)
    return session.resource(service, region_name=region)


def check_credentials(profile: str | None = None) -> bool:
    """
    Check if AWS credentials are valid.

    Args:
        profile: AWS profile name.

    Returns:
        True if credentials are valid.
    """
    try:
        session = get_session(profile)
        sts = session.client("sts")
        sts.get_caller_identity()
        return True
    except (ClientError, BotoCoreError) as e:
        print_error(f"AWS credentials check failed: {e}")
        return False


def get_account_id(profile: str | None = None) -> str | None:
    """
    Get the AWS account ID.

    Args:
        profile: AWS profile name.

    Returns:
        Account ID or None if failed.
    """
    try:
        session = get_session(profile)
        sts = session.client("sts")
        identity = sts.get_caller_identity()
        return identity["Account"]
    except Exception as e:
        print_error(f"Failed to get account ID: {e}")
        return None


def get_available_regions(profile: str | None = None) -> list:
    """
    Get list of available AWS regions.

    Args:
        profile: AWS profile name.

    Returns:
        List of region names.
    """
    try:
        session = get_session(profile)
        ec2 = session.client("ec2", region_name="us-east-1")
        response = ec2.describe_regions()
        return sorted([r["RegionName"] for r in response["Regions"]])
    except Exception as e:
        print_error(f"Failed to get regions: {e}")
        # Return common regions as fallback
        return [
            "us-east-1", "us-east-2", "us-west-1", "us-west-2",
            "eu-west-1", "eu-west-2", "eu-central-1",
            "ap-northeast-1", "ap-southeast-1", "ap-southeast-2",
        ]


