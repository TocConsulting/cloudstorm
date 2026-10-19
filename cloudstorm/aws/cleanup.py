"""
AWS resource cleanup for cloudstorm.
"""
from typing import Any

from ..constants import TAG_CREATED_BY, TAG_CREATED_BY_VALUE
from ..utils.display import print_error, print_info, print_success, print_warning
from ..utils.parallel import run_parallel_regions
from .credentials import get_available_regions, get_client
from .ec2 import get_instances, terminate_instances


def cleanup_region(
    region: str,
    profile: str | None = None,
) -> dict[str, Any]:
    """
    Cleanup all cloudstorm resources in a region.

    Args:
        region: AWS region.
        profile: AWS profile name.

    Returns:
        Dictionary with cleanup results.
    """
    results = {
        "region": region,
        "instances_terminated": 0,
        "security_groups_deleted": 0,
        "vpcs_deleted": 0,
        "errors": [],
    }

    try:
        # Terminate instances
        instances = get_instances(region, profile)
        if instances:
            instance_ids = [i["instance_id"] for i in instances]
            if terminate_instances(region, instance_ids, profile):
                results["instances_terminated"] = len(instance_ids)
            else:
                results["errors"].append("Failed to terminate instances - skipping SG/VPC cleanup")
                return results

        # Delete security groups
        deleted_sgs = _cleanup_security_groups(region, profile)
        results["security_groups_deleted"] = deleted_sgs

        # Delete VPCs (only cloudstorm-created ones)
        deleted_vpcs = _cleanup_vpcs(region, profile)
        results["vpcs_deleted"] = deleted_vpcs

    except Exception as e:
        results["errors"].append(str(e))

    return results


def cleanup_all(
    regions: list[str] | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """
    Cleanup all cloudstorm resources across regions.

    Args:
        regions: List of regions (None = check all).
        profile: AWS profile name.

    Returns:
        Dictionary with cleanup results by region.
    """
    if not regions:
        # Get all regions with cloudstorm resources
        regions = find_regions_with_resources(profile)

    if not regions:
        print_info("No cloudstorm resources found")
        return {}

    print_info(f"Cleaning up resources in {len(regions)} regions")

    def cleanup_fn(region):
        return cleanup_region(region, profile)

    results = run_parallel_regions(cleanup_fn, regions)

    # Summary
    total_instances = sum(r["instances_terminated"] for r in results.values() if r)
    total_sgs = sum(r["security_groups_deleted"] for r in results.values() if r)
    total_vpcs = sum(r["vpcs_deleted"] for r in results.values() if r)

    # Cleanup IAM resources (global, only once)
    _cleanup_iam(profile)

    print_success(f"Cleanup complete: {total_instances} instances, {total_sgs} security groups, {total_vpcs} VPCs")

    return results


def find_regions_with_resources(profile: str | None = None) -> list[str]:
    """
    Find regions that have cloudstorm resources.

    Args:
        profile: AWS profile name.

    Returns:
        List of region names.
    """
    all_regions = get_available_regions(profile)
    regions_with_resources = []

    def check_region(region):
        # Check instances
        if get_instances(region, profile):
            return True
        # Check orphaned SGs and VPCs
        try:
            ec2 = get_client("ec2", region, profile)
            sgs = ec2.describe_security_groups(
                Filters=[{"Name": f"tag:{TAG_CREATED_BY}", "Values": [TAG_CREATED_BY_VALUE]}]
            )
            if sgs.get("SecurityGroups"):
                return True
            vpcs = ec2.describe_vpcs(
                Filters=[{"Name": f"tag:{TAG_CREATED_BY}", "Values": [TAG_CREATED_BY_VALUE]}]
            )
            if vpcs.get("Vpcs"):
                return True
        except Exception:
            pass
        return False

    results = run_parallel_regions(check_region, all_regions)

    for region, has_resources in results.items():
        if has_resources:
            regions_with_resources.append(region)

    return regions_with_resources


def get_all_instances(profile: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """
    Get all cloudstorm instances across all regions.

    Args:
        profile: AWS profile name.

    Returns:
        Dictionary mapping regions to instance lists.
    """
    all_regions = get_available_regions(profile)
    instances_by_region = {}

    def get_region_instances(region):
        return get_instances(region, profile)

    results = run_parallel_regions(get_region_instances, all_regions)

    for region, instances in results.items():
        if instances:
            instances_by_region[region] = instances

    return instances_by_region


def _cleanup_iam(profile: str | None = None) -> bool:
    """Delete cloudstorm IAM role and instance profile."""
    try:
        iam = get_client("iam", "us-east-1", profile)
        role_name = "cloudstorm-ssm-role"
        profile_name = "cloudstorm-ssm-profile"

        try:
            iam.remove_role_from_instance_profile(
                InstanceProfileName=profile_name, RoleName=role_name
            )
        except iam.exceptions.NoSuchEntityException:
            pass  # Already removed
        except Exception as e:
            print_warning(f"Could not remove role from instance profile: {e}")

        try:
            iam.delete_instance_profile(InstanceProfileName=profile_name)
            print_info(f"Deleted instance profile: {profile_name}")
        except iam.exceptions.NoSuchEntityException:
            pass  # Already deleted
        except Exception as e:
            print_warning(f"Could not delete instance profile: {e}")

        try:
            iam.detach_role_policy(
                RoleName=role_name,
                PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
            )
        except iam.exceptions.NoSuchEntityException:
            pass  # Already detached
        except Exception as e:
            print_warning(f"Could not detach role policy: {e}")

        try:
            iam.delete_role(RoleName=role_name)
            print_info(f"Deleted IAM role: {role_name}")
        except iam.exceptions.NoSuchEntityException:
            pass  # Already deleted
        except Exception as e:
            print_warning(f"Could not delete IAM role: {e}")

        return True
    except Exception as e:
        print_warning(f"IAM cleanup error: {e}")
        return False


def _cleanup_security_groups(region: str, profile: str | None = None) -> int:
    """Delete cloudstorm security groups in a region."""
    deleted = 0

    try:
        ec2 = get_client("ec2", region, profile)

        response = ec2.describe_security_groups(
            Filters=[
                {"Name": f"tag:{TAG_CREATED_BY}", "Values": [TAG_CREATED_BY_VALUE]},
            ]
        )

        for sg in response.get("SecurityGroups", []):
            sg_id = sg["GroupId"]

            # Don't delete default security groups
            if sg.get("GroupName") == "default":
                continue

            try:
                ec2.delete_security_group(GroupId=sg_id)
                deleted += 1
                print_info(f"Deleted security group {sg_id} in {region}")
            except Exception as e:
                # May fail if still in use, that's okay
                print_warning(f"Could not delete security group {sg_id}: {e}")

    except Exception as e:
        print_error(f"Error cleaning up security groups in {region}: {e}")

    return deleted


def _cleanup_vpcs(region: str, profile: str | None = None) -> int:
    """Delete cloudstorm VPCs in a region."""
    deleted = 0

    try:
        ec2 = get_client("ec2", region, profile)

        response = ec2.describe_vpcs(
            Filters=[
                {"Name": f"tag:{TAG_CREATED_BY}", "Values": [TAG_CREATED_BY_VALUE]},
            ]
        )

        for vpc in response.get("Vpcs", []):
            vpc_id = vpc["VpcId"]

            # Don't delete default VPCs
            if vpc.get("IsDefault"):
                continue

            if _delete_vpc(ec2, vpc_id, region):
                deleted += 1

    except Exception as e:
        print_error(f"Error cleaning up VPCs in {region}: {e}")

    return deleted


def _delete_vpc(ec2, vpc_id: str, region: str) -> bool:
    """Delete a VPC and all its dependencies."""
    try:
        # Delete subnets
        subnets = ec2.describe_subnets(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        )

        for subnet in subnets.get("Subnets", []):
            try:
                ec2.delete_subnet(SubnetId=subnet["SubnetId"])
            except Exception as e:
                print_warning(f"Could not delete subnet {subnet['SubnetId']}: {e}")

        # Delete route tables (non-main)
        route_tables = ec2.describe_route_tables(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        )

        for rt in route_tables.get("RouteTables", []):
            # Skip main route table
            is_main = any(
                assoc.get("Main", False)
                for assoc in rt.get("Associations", [])
            )
            if is_main:
                continue

            # Delete associations first
            for assoc in rt.get("Associations", []):
                if not assoc.get("Main", False):
                    try:
                        ec2.disassociate_route_table(
                            AssociationId=assoc["RouteTableAssociationId"]
                        )
                    except Exception as e:
                        print_warning(f"Could not disassociate route table: {e}")

            try:
                ec2.delete_route_table(RouteTableId=rt["RouteTableId"])
            except Exception as e:
                print_warning(f"Could not delete route table {rt['RouteTableId']}: {e}")

        # Detach and delete internet gateways
        igws = ec2.describe_internet_gateways(
            Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]
        )

        for igw in igws.get("InternetGateways", []):
            try:
                ec2.detach_internet_gateway(
                    InternetGatewayId=igw["InternetGatewayId"],
                    VpcId=vpc_id,
                )
                ec2.delete_internet_gateway(
                    InternetGatewayId=igw["InternetGatewayId"]
                )
            except Exception as e:
                print_warning(f"Could not delete internet gateway: {e}")

        # Delete security groups (non-default)
        sgs = ec2.describe_security_groups(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        )

        for sg in sgs.get("SecurityGroups", []):
            if sg.get("GroupName") != "default":
                try:
                    ec2.delete_security_group(GroupId=sg["GroupId"])
                except Exception as e:
                    print_warning(f"Could not delete security group {sg['GroupId']}: {e}")

        # Delete VPC
        ec2.delete_vpc(VpcId=vpc_id)
        print_info(f"Deleted VPC {vpc_id} in {region}")

        return True

    except Exception as e:
        print_warning(f"Could not delete VPC {vpc_id}: {e}")
        return False
