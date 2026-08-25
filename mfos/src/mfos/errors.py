"""Typed halts.

The pipeline is built to stop rather than to guess. Each exception below is a
distinct reason to stop, so the halt can be logged, explained to the investor
and audited without the caller having to parse a message string.
"""


class MFOSError(Exception):
    """Base class. Carries a machine-readable code and a citable reason."""

    code = "MFOS_ERROR"

    def __init__(self, message, *, detail=None, rule_ids=None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}
        self.rule_ids = list(rule_ids or [])

    def as_dict(self):
        return {
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
            "rule_ids": self.rule_ids,
        }


class InsufficientData(MFOSError):
    """We do not have enough verified data. Ask; never fill the gap in."""

    code = "INSUFFICIENT_DATA"


class DataQualityFailure(MFOSError):
    """DATA QUALITY OVERRIDES AI."""

    code = "DATA_QUALITY_FAILURE"


class ComplianceBlock(MFOSError):
    """COMPLIANCE OVERRIDES AI."""

    code = "COMPLIANCE_BLOCK"


class SuitabilityBlock(MFOSError):
    """SUITABILITY OVERRIDES PERFORMANCE."""

    code = "SUITABILITY_BLOCK"


class ConsentRequired(MFOSError):
    """No specific, per-transaction consent -> no transaction."""

    code = "CONSENT_REQUIRED"


class RuleNotVerified(MFOSError):
    """A rule exists but has not been verified against its source."""

    code = "RULE_NOT_VERIFIED"


class ConfigError(MFOSError):
    code = "CONFIG_ERROR"
