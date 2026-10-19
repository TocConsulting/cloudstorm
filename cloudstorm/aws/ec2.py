"""
EC2 instance management for cloudstorm.
"""
import json
import time
from typing import Any

from botocore.exceptions import ClientError

from ..constants import (
    DEFAULT_AMI_NAME_PATTERN,
    TAG_CREATED_BY,
    TAG_CREATED_BY_VALUE,
    TAG_PURPOSE,
    TAG_PURPOSE_VALUE,
)
from ..utils.display import print_error, print_info, print_success, print_warning
from .credentials import get_client, get_resource


def get_latest_ami(region: str, profile: str | None = None) -> str | None:
    """
    Get the latest Amazon Linux 2023 AMI ID.

    Args:
        region: AWS region.
        profile: AWS profile name.

    Returns:
        AMI ID or None if not found.
    """
    try:
        ec2 = get_client("ec2", region, profile)

        response = ec2.describe_images(
            Owners=["amazon"],
            Filters=[
                {"Name": "name", "Values": [DEFAULT_AMI_NAME_PATTERN]},
                {"Name": "state", "Values": ["available"]},
                {"Name": "architecture", "Values": ["x86_64"]},
            ],
        )

        if not response["Images"]:
            print_error(f"No AMI found in {region}")
            return None

        # Sort by creation date and get the latest
        images = sorted(response["Images"], key=lambda x: x["CreationDate"], reverse=True)
        ami_id = images[0]["ImageId"]

        return ami_id

    except Exception as e:
        print_error(f"Failed to get AMI in {region}: {e}")
        return None


def ensure_iam_role(region: str, profile: str | None = None) -> dict[str, str] | None:
    """
    Ensure IAM role and instance profile exist for SSM.

    Args:
        region: AWS region.
        profile: AWS profile name.

    Returns:
        Dictionary with role info or None if failed.
    """
    try:
        iam = get_client("iam", region, profile)

        role_name = "cloudstorm-ssm-role"
        profile_name = "cloudstorm-ssm-profile"

        # Check/create role
        try:
            iam.get_role(RoleName=role_name)
        except iam.exceptions.NoSuchEntityException:
            try:
                trust_policy = {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "ec2.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }

                iam.create_role(
                    RoleName=role_name,
                    AssumeRolePolicyDocument=json.dumps(trust_policy),
                    Description="Role for cloudstorm instances to use SSM",
                )
                print_success(f"Created IAM role: {role_name}")
            except iam.exceptions.EntityAlreadyExistsException:
                pass  # Created by another thread

        # Ensure policy is attached (idempotent -- no error if already attached)
        try:
            iam.attach_role_policy(
                RoleName=role_name,
                PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
            )
        except Exception:
            pass  # Already attached or role just created by another thread

        # Check/create instance profile
        created_profile = False
        try:
            response = iam.get_instance_profile(InstanceProfileName=profile_name)
            profile_arn = response["InstanceProfile"]["Arn"]
        except iam.exceptions.NoSuchEntityException:
            try:
                response = iam.create_instance_profile(InstanceProfileName=profile_name)
                profile_arn = response["InstanceProfile"]["Arn"]
                created_profile = True
                print_success(f"Created instance profile: {profile_name}")
            except iam.exceptions.EntityAlreadyExistsException:
                response = iam.get_instance_profile(InstanceProfileName=profile_name)
                profile_arn = response["InstanceProfile"]["Arn"]

        # Ensure role is attached to profile (idempotent)
        try:
            iam.add_role_to_instance_profile(
                InstanceProfileName=profile_name,
                RoleName=role_name,
            )
        except iam.exceptions.LimitExceededException:
            pass  # Role already attached

        # IAM is eventually consistent - wait for propagation on new profiles
        if created_profile:
            for attempt in range(12):
                time.sleep(5)
                try:
                    iam.get_instance_profile(InstanceProfileName=profile_name)
                    break
                except Exception:
                    if attempt == 11:
                        print_warning("IAM profile may not be fully propagated yet")

        return {
            "role_name": role_name,
            "profile_name": profile_name,
            "profile_arn": profile_arn,
        }

    except Exception as e:
        print_error(f"Failed to setup IAM role: {e}")
        return None


def create_instances(
    region: str,
    count: int,
    instance_type: str,
    subnet_id: str,
    security_group_id: str,
    profile: str | None = None,
    iam_profile_arn: str | None = None,
) -> list[str]:
    """
    Create EC2 instances.

    Args:
        region: AWS region.
        count: Number of instances.
        instance_type: EC2 instance type.
        subnet_id: Subnet ID.
        security_group_id: Security group ID.
        profile: AWS profile name.
        iam_profile_arn: Pre-created IAM instance profile ARN.
            If None, ensure_iam_role will be called.

    Returns:
        List of instance IDs.
    """
    try:
        # Get AMI
        ami_id = get_latest_ami(region, profile)
        if not ami_id:
            return []

        # Ensure IAM role
        if iam_profile_arn:
            profile_arn = iam_profile_arn
        else:
            iam_info = ensure_iam_role(region, profile)
            if not iam_info:
                return []
            profile_arn = iam_info["profile_arn"]

        # Load user data script
        user_data = _get_user_data_script()

        # Create instances with retry for IAM propagation delay
        ec2 = get_resource("ec2", region, profile)

        launch_kwargs = {
            "ImageId": ami_id,
            "InstanceType": instance_type,
            "MinCount": count,
            "MaxCount": count,
            "IamInstanceProfile": {"Arn": profile_arn},
            "BlockDeviceMappings": [
                {
                    "DeviceName": "/dev/xvda",
                    "Ebs": {
                        "VolumeSize": 30,
                        "VolumeType": "gp3",
                        "DeleteOnTermination": True,
                    },
                }
            ],
            "NetworkInterfaces": [
                {
                    "DeviceIndex": 0,
                    "SubnetId": subnet_id,
                    "AssociatePublicIpAddress": True,
                    "Groups": [security_group_id],
                }
            ],
            "TagSpecifications": [
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "Name", "Value": f"cloudstorm-{region}"},
                        {"Key": TAG_CREATED_BY, "Value": TAG_CREATED_BY_VALUE},
                        {"Key": TAG_PURPOSE, "Value": TAG_PURPOSE_VALUE},
                    ],
                }
            ],
            "UserData": user_data,
        }

        max_attempts = 8
        for attempt in range(max_attempts):
            try:
                instances = ec2.create_instances(**launch_kwargs)
                break
            except ClientError as e:
                code = e.response["Error"]["Code"]
                msg = e.response["Error"].get("Message", "")
                if code == "InvalidParameterValue" and "Invalid IAM Instance Profile" in msg:
                    if attempt < max_attempts - 1:
                        delay = min(5 * (2 ** attempt), 60)
                        print_warning(f"{region}: IAM profile not propagated to EC2 yet, "
                                     f"retrying in {delay}s ({attempt + 1}/{max_attempts})")
                        time.sleep(delay)
                    else:
                        print_error(f"IAM profile not propagated after {max_attempts} attempts in {region}")
                        return []
                else:
                    print_error(f"AWS error in {region}: {code} - {msg}")
                    return []

        instance_ids = [i.id for i in instances]
        print_success(f"Created {count} instances in {region}: {', '.join(instance_ids)}")

        return instance_ids

    except Exception as e:
        print_error(f"Failed to create instances in {region}: {e}")
        return []


def _get_user_data_script() -> str:
    """Get the instance initialization script."""
    return """#!/bin/bash
exec > /var/log/user-data.log 2>&1
set -x
dnf update -y
dnf install -y amazon-ssm-agent python3 python3-pip gcc git
systemctl enable amazon-ssm-agent
systemctl restart amazon-ssm-agent
touch /tmp/cloudstorm-ready
"""


def get_instances(
    region: str,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """
    Get all cloudstorm instances in a region.

    Args:
        region: AWS region.
        profile: AWS profile name.

    Returns:
        List of instance details.
    """
    try:
        ec2 = get_client("ec2", region, profile)

        response = ec2.describe_instances(
            Filters=[
                {"Name": f"tag:{TAG_CREATED_BY}", "Values": [TAG_CREATED_BY_VALUE]},
                {"Name": "instance-state-name", "Values": ["pending", "running"]},
            ]
        )

        instances = []
        for reservation in response.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                instances.append({
                    "instance_id": instance["InstanceId"],
                    "instance_type": instance["InstanceType"],
                    "state": instance["State"]["Name"],
                    "public_ip": instance.get("PublicIpAddress", ""),
                    "private_ip": instance.get("PrivateIpAddress", ""),
                    "launch_time": instance.get("LaunchTime"),
                    "region": region,
                })

        return instances

    except Exception as e:
        print_error(f"Failed to get instances in {region}: {e}")
        return []


def terminate_instances(
    region: str,
    instance_ids: list[str],
    profile: str | None = None,
) -> bool:
    """
    Terminate EC2 instances.

    Args:
        region: AWS region.
        instance_ids: List of instance IDs.
        profile: AWS profile name.

    Returns:
        True if successful.
    """
    if not instance_ids:
        return True

    try:
        ec2 = get_client("ec2", region, profile)
        ec2.terminate_instances(InstanceIds=instance_ids)

        print_info(f"Terminating {len(instance_ids)} instances in {region}")

        # Wait for termination
        waiter = ec2.get_waiter("instance_terminated")
        waiter.wait(InstanceIds=instance_ids)

        print_success(f"Terminated instances in {region}")
        return True

    except Exception as e:
        print_error(f"Failed to terminate instances in {region}: {e}")
        return False
