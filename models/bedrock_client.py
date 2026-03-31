import json
import os
from pathlib import Path

import boto3
from dotenv import load_dotenv

# Load .env from project root (two levels up from models/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# Resolve logger — works whether called from notebooks/ or project root
import sys
sys.path.insert(0, str(_PROJECT_ROOT / "notebooks"))
from utils import get_logger
logger = get_logger("bedrock_client")

# ── Model catalogue ───────────────────────────────────────────
_CONFIG_PATH = _PROJECT_ROOT / "config" / "models.json"


def _load_model_config() -> dict:
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_available_models() -> dict[str, str]:
    """Return {friendly_name: model_id} dict."""
    return _load_model_config()["models"]


def get_default_model_id() -> str:
    """Return the Bedrock model ID for the configured default model."""
    cfg = _load_model_config()
    default_name = cfg["default"]
    return cfg["models"][default_name]


def resolve_model_id(model_name: str | None = None) -> str:
    """
    Resolve a friendly model name to a Bedrock model ID.

    If model_name is None, returns the default.
    If model_name is already a full model ID (contains 'anthropic'), returns it as-is.
    """
    if model_name is None:
        return get_default_model_id()
    # Already a raw model ID
    if "anthropic" in model_name:
        return model_name
    models = get_available_models()
    if model_name in models:
        return models[model_name]
    raise ValueError(
        f"Unknown model '{model_name}'. "
        f"Available: {list(models.keys())}"
    )


# ── Credentials ───────────────────────────────────────────────

def _get_bedrock_credentials() -> dict:
    """
    Build boto3 client kwargs for Bedrock.

    Resolution order:
    1. If BEDROCK_ROLE_ARN is set → STS assume-role (works in Lambda,
       SageMaker, local with any base credentials).
    2. If explicit AWS_ACCESS_KEY_ID is set → use those directly.
    3. Otherwise → empty dict (boto3 default chain: env vars,
       ~/.aws/credentials, instance/task role).
    """
    aws_region = os.getenv("AWS_REGION", "us-east-2")
    bedrock_role_arn = os.getenv("BEDROCK_ROLE_ARN", "").strip()

    # ── Strategy 1: STS assume-role ──
    if bedrock_role_arn:
        logger.info("Assuming Bedrock role: %s", bedrock_role_arn)
        try:
            sts = boto3.client("sts", region_name=aws_region)
            assumed = sts.assume_role(
                RoleArn=bedrock_role_arn,
                RoleSessionName="BedrockInvocationSession",
                DurationSeconds=3600,
            )
            creds = assumed["Credentials"]
            logger.info("Successfully assumed Bedrock role")
            return {
                "aws_access_key_id": creds["AccessKeyId"],
                "aws_secret_access_key": creds["SecretAccessKey"],
                "aws_session_token": creds["SessionToken"],
                "region_name": aws_region,
            }
        except Exception as exc:
            logger.error("Failed to assume Bedrock role: %s", exc)
            raise

    # ── Strategy 2: explicit .env credentials ──
    access_key = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
    if access_key and secret_key:
        logger.info("Using explicit AWS credentials from environment")
        session_token = os.getenv("AWS_SESSION_TOKEN", "").strip() or None
        return {
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
            "aws_session_token": session_token,
            "region_name": aws_region,
        }

    # ── Strategy 3: boto3 default chain ──
    logger.info("Using default AWS credentials chain (env / profile / instance role)")
    return {"region_name": aws_region}


# ── Client factories ──────────────────────────────────────────

def get_bedrock_client(**overrides):
    """
    Return a bedrock-runtime client.

    Accepts optional boto3.client kwargs to override defaults
    (e.g. region_name).
    """
    logger.info("Initializing Bedrock runtime client")
    try:
        kwargs = _get_bedrock_credentials()
        kwargs.update(overrides)
        return boto3.client("bedrock-runtime", **kwargs)
    except Exception:
        logger.exception("Failed to initialize Bedrock runtime client")
        raise


def get_bedrock_agent_client(**overrides):
    """Return a bedrock-agent-runtime client."""
    logger.info("Initializing Bedrock Agent runtime client")
    try:
        kwargs = _get_bedrock_credentials()
        kwargs.update(overrides)
        return boto3.client("bedrock-agent-runtime", **kwargs)
    except Exception:
        logger.exception("Failed to initialize Bedrock Agent client")
        raise
