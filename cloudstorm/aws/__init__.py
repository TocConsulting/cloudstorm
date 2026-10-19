"""
AWS operations for cloudstorm.
"""
from .cleanup import (
    cleanup_all,
    cleanup_region,
)
from .credentials import check_credentials, get_account_id, get_session
from .ec2 import (
    create_instances,
    ensure_iam_role,
    get_instances,
    get_latest_ami,
    terminate_instances,
)
from .ssm import (
    get_ssm_ready_instances,
    run_command,
    wait_for_ssm_ready,
)
from .vpc import (
    get_or_create_security_group,
    setup_vpc,
)

__all__ = [
    "get_session",
    "check_credentials",
    "get_account_id",
    "create_instances",
    "terminate_instances",
    "get_instances",
    "get_latest_ami",
    "ensure_iam_role",
    "run_command",
    "wait_for_ssm_ready",
    "get_ssm_ready_instances",
    "setup_vpc",
    "get_or_create_security_group",
    "cleanup_region",
    "cleanup_all",
]
