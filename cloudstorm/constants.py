"""
Constants for cloudstorm.
"""
from pathlib import Path

# Application info
APP_NAME = "cloudstorm"

# Paths
CONFIG_DIR = Path.home() / ".cloudstorm"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
RESULTS_DIR = CONFIG_DIR / "results"

# AWS Tags
TAG_CREATED_BY = "CreatedBy"
TAG_CREATED_BY_VALUE = "cloudstorm"
TAG_PURPOSE = "Purpose"
TAG_PURPOSE_VALUE = "LoadTesting"

# Supported tools
SUPPORTED_TOOLS = ["locust", "jmeter", "artillery", "k6"]

# Default EC2 settings
DEFAULT_INSTANCE_TYPE = "t3.medium"
DEFAULT_AMI_NAME_PATTERN = "al2023-ami-*-kernel-*-x86_64"

# Timeouts (seconds)
TIMEOUT_TOOL_INSTALL = 300
TIMEOUT_TEST_BUFFER = 120

# Polling intervals (seconds)
POLL_INTERVAL_FAST = 5
POLL_INTERVAL_NORMAL = 10

# EC2 pricing estimates (USD per hour, approximate)
# https://aws.amazon.com/ec2/pricing/on-demand/
# EC2 instance info: price (USD/hour) and memory (GiB)
EC2_INSTANCE_INFO = {
    "t3.micro":    {"price": 0.0104, "memory_gib": 1},
    "t3.small":    {"price": 0.0208, "memory_gib": 2},
    "t3.medium":   {"price": 0.0416, "memory_gib": 4},
    "t3.large":    {"price": 0.0832, "memory_gib": 8},
    "t3.xlarge":   {"price": 0.1664, "memory_gib": 16},
    "t3.2xlarge":  {"price": 0.3328, "memory_gib": 32},
    "m5.large":    {"price": 0.096,  "memory_gib": 8},
    "m5.xlarge":   {"price": 0.192,  "memory_gib": 16},
    "m5.2xlarge":  {"price": 0.384,  "memory_gib": 32},
    "c5.large":    {"price": 0.085,  "memory_gib": 4},
    "c5.xlarge":   {"price": 0.17,   "memory_gib": 8},
    "c5.2xlarge":  {"price": 0.34,   "memory_gib": 16},
}

# Backwards-compatible pricing lookup
EC2_PRICING = {k: v["price"] for k, v in EC2_INSTANCE_INFO.items()}

# Safe max users per GiB of RAM per tool
TOOL_USERS_PER_GIB = {
    "locust": 750,
    "k6": 2500,
    "jmeter": 500,
    "artillery": 1200,
}

# Default config
DEFAULT_CONFIG = {
    "aws": {
        "profile": None,
        "instance_type": DEFAULT_INSTANCE_TYPE,
    },
    "test": {
        "tool": "locust",
        "users": 100,
        "spawn_rate": 10,
        "duration": "5m",
    },
    "cleanup": {
        "auto_cleanup": False,
    },
}
