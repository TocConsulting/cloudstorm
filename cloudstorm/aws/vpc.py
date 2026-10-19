"""
VPC and networking operations for cloudstorm.
"""
from typing import Any

from ..constants import TAG_CREATED_BY, TAG_CREATED_BY_VALUE
from ..utils.display import print_error, print_success
from .credentials import get_client


def setup_vpc(
    region: str,
    profile: str | None = None,
) -> dict[str, Any]:
    """
    Setup VPC for cloudstorm instances.
    Uses default VPC if available, creates minimal VPC if not.

    Args:
        region: AWS region.
        profile: AWS profile name.

    Returns:
        Dictionary with vpc_id, subnet_id.
    """
    try:
        ec2 = get_client("ec2", region, profile)

        # Try to use default VPC
        default_vpc = _get_default_vpc(ec2)

        if default_vpc:
            subnet_id = _get_default_subnet(ec2, default_vpc)
            if subnet_id:
                return {
                    "vpc_id": default_vpc,
                    "subnet_id": subnet_id,
                }

        # Check for existing cloudstorm-tagged VPC (from previous create/add)
        existing = ec2.describe_vpcs(
            Filters=[{"Name": f"tag:{TAG_CREATED_BY}", "Values": [TAG_CREATED_BY_VALUE]}]
        )
        for vpc in existing.get("Vpcs", []):
            vpc_id = vpc["VpcId"]
            subnets = ec2.describe_subnets(
                Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
            )
            if subnets.get("Subnets"):
                return {
                    "vpc_id": vpc_id,
                    "subnet_id": subnets["Subnets"][0]["SubnetId"],
                }

        # No existing VPC, create a simple one
        return _create_simple_vpc(ec2, region)

    except Exception as e:
        print_error(f"Failed to setup VPC in {region}: {e}")
        return {"error": str(e)}


def _get_default_vpc(ec2) -> str | None:
    """Get the default VPC ID."""
    try:
        response = ec2.describe_vpcs(
            Filters=[{"Name": "is-default", "Values": ["true"]}]
        )

        if response["Vpcs"]:
            return response["Vpcs"][0]["VpcId"]

        return None

    except Exception:
        return None


def _get_default_subnet(ec2, vpc_id: str) -> str | None:
    """Get a subnet from the default VPC."""
    try:
        response = ec2.describe_subnets(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "default-for-az", "Values": ["true"]},
            ]
        )

        if response["Subnets"]:
            # Prefer subnets with auto-assign public IP
            for subnet in response["Subnets"]:
                if subnet.get("MapPublicIpOnLaunch"):
                    return subnet["SubnetId"]

            # Otherwise return first available
            return response["Subnets"][0]["SubnetId"]

        return None

    except Exception:
        return None


def _create_simple_vpc(ec2, region: str) -> dict[str, Any]:
    """Create a simple VPC for cloudstorm."""
    try:
        # Create VPC
        vpc_response = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        vpc_id = vpc_response["Vpc"]["VpcId"]

        # Tag VPC
        ec2.create_tags(
            Resources=[vpc_id],
            Tags=[
                {"Key": "Name", "Value": "cloudstorm-vpc"},
                {"Key": TAG_CREATED_BY, "Value": TAG_CREATED_BY_VALUE},
            ],
        )

        # Enable DNS
        ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
        ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})

        # Create internet gateway
        igw_response = ec2.create_internet_gateway()
        igw_id = igw_response["InternetGateway"]["InternetGatewayId"]

        ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)

        ec2.create_tags(
            Resources=[igw_id],
            Tags=[
                {"Key": "Name", "Value": "cloudstorm-igw"},
                {"Key": TAG_CREATED_BY, "Value": TAG_CREATED_BY_VALUE},
            ],
        )

        # Create subnet
        subnet_response = ec2.create_subnet(VpcId=vpc_id, CidrBlock="10.0.1.0/24")
        subnet_id = subnet_response["Subnet"]["SubnetId"]

        # Enable auto-assign public IP
        ec2.modify_subnet_attribute(
            SubnetId=subnet_id,
            MapPublicIpOnLaunch={"Value": True},
        )

        ec2.create_tags(
            Resources=[subnet_id],
            Tags=[
                {"Key": "Name", "Value": "cloudstorm-subnet"},
                {"Key": TAG_CREATED_BY, "Value": TAG_CREATED_BY_VALUE},
            ],
        )

        # Create route table
        rt_response = ec2.create_route_table(VpcId=vpc_id)
        rt_id = rt_response["RouteTable"]["RouteTableId"]

        ec2.create_route(
            RouteTableId=rt_id,
            DestinationCidrBlock="0.0.0.0/0",
            GatewayId=igw_id,
        )

        ec2.associate_route_table(RouteTableId=rt_id, SubnetId=subnet_id)

        ec2.create_tags(
            Resources=[rt_id],
            Tags=[
                {"Key": "Name", "Value": "cloudstorm-rt"},
                {"Key": TAG_CREATED_BY, "Value": TAG_CREATED_BY_VALUE},
            ],
        )

        print_success(f"Created VPC in {region}")

        return {
            "vpc_id": vpc_id,
            "subnet_id": subnet_id,
            "created": True,
        }

    except Exception as e:
        print_error(f"Failed to create VPC in {region}: {e}")
        return {"error": str(e)}


def get_or_create_security_group(
    region: str,
    vpc_id: str,
    profile: str | None = None,
) -> str | None:
    """
    Get or create security group for cloudstorm.

    Args:
        region: AWS region.
        vpc_id: VPC ID.
        profile: AWS profile name.

    Returns:
        Security group ID or None.
    """
    try:
        ec2 = get_client("ec2", region, profile)

        # Check if security group exists
        response = ec2.describe_security_groups(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "group-name", "Values": ["cloudstorm-sg"]},
            ]
        )

        if response["SecurityGroups"]:
            return response["SecurityGroups"][0]["GroupId"]

        # Create security group
        sg_response = ec2.create_security_group(
            GroupName="cloudstorm-sg",
            Description="Security group for cloudstorm load testing",
            VpcId=vpc_id,
        )

        sg_id = sg_response["GroupId"]

        # Tag it
        ec2.create_tags(
            Resources=[sg_id],
            Tags=[
                {"Key": "Name", "Value": "cloudstorm-sg"},
                {"Key": TAG_CREATED_BY, "Value": TAG_CREATED_BY_VALUE},
            ],
        )

        # Add egress rule (allow all outbound - needed for load testing)
        # Default egress allows all, but let's be explicit
        try:
            ec2.authorize_security_group_egress(
                GroupId=sg_id,
                IpPermissions=[
                    {
                        "IpProtocol": "-1",
                        "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                    }
                ],
            )
        except Exception:
            pass  # Default egress rule might already exist

        print_success(f"Created security group in {region}")

        return sg_id

    except Exception as e:
        print_error(f"Failed to create security group in {region}: {e}")
        return None
