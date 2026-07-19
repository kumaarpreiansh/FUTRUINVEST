"""Optional LLM layer — wires committee roles to Claude via the Anthropic API.

Each wired role sends its agents/<role>/PROMPT.md as the system prompt plus
the research brief as the user message, and gets a structured thesis back
(evidence, confidence, invalidation) via structured outputs.

Offline-safe by design: if no API key is configured, the SDK is missing, or
a call fails for any reason, functions return None and callers fall back to
the deterministic stand-ins in main.py. Set ANTHROPIC_API_KEY (or
LLM_API_KEY) in .env to activate.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

DEFAULT_MODEL = "claude-opus-4-8"


def _load_env():
    """Minimal .env loader; real environment variables take precedence."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def api_key():
    _load_env()
    return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("LLM_API_KEY")


def llm_enabled():
    return bool(api_key())


def run_llm_agent(role, brief, cfg=None):
    """Run one committee role against Claude.

    Returns {"evidence": [...], "confidence": int, "invalidation": str},
    or None so the caller falls back to its deterministic stand-in.
    """
    key = api_key()
    if not key:
        return None
    try:
        import anthropic
        from pydantic import BaseModel, Field

        class Thesis(BaseModel):
            evidence: list[str] = Field(
                description="Evidence-backed points for this side's thesis, each "
                "citing figures from the brief; empty if the data does not "
                "support the thesis"
            )
            confidence: int = Field(ge=0, le=100, description="Confidence score 0-100")
            invalidation: str = Field(
                description="The exact conditions that would invalidate this thesis"
            )

        system = (ROOT / "agents" / role / "PROMPT.md").read_text(encoding="utf-8")
        system = system.replace("[ASSET]", str(brief.get("asset", "the asset")))

        llm_cfg = (cfg or {}).get("agents", {}).get("llm") or {}
        model = llm_cfg.get("model", DEFAULT_MODEL)

        client = anthropic.Anthropic(api_key=key)
        response = client.messages.parse(
            model=model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=system,
            messages=[{
                "role": "user",
                "content": (
                    "Research brief — the ONLY data you may use; cite these "
                    "numbers and invent nothing:\n" + json.dumps(brief, indent=2)
                ),
            }],
            output_format=Thesis,
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            print(f"  ({role} agent: LLM declined — using deterministic fallback)",
                  file=sys.stderr)
            return None
        thesis = response.parsed_output
        return {
            "evidence": list(thesis.evidence),
            "confidence": max(0, min(100, thesis.confidence)),
            "invalidation": thesis.invalidation,
        }
    except Exception as exc:
        print(f"  ({role} agent: LLM call failed ({type(exc).__name__}) — "
              f"using deterministic fallback)", file=sys.stderr)
        return None
