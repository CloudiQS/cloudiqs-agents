"""
Reply Classifier — classify Instantly email replies using Bedrock Haiku.

Classification labels:
  interested     — Positive, wants to learn more or book a call
  not_now        — Not ready now but maybe later
  not_relevant   — Wrong product/service/audience
  referral       — Asking to speak to someone else or be redirected
  ooo            — Out-of-office auto-reply
  bounce         — Email bounced or address invalid
  unsubscribe    — Asking to be removed
  unknown        — Cannot determine

Exported:
  classify_reply(reply_text, email, campaign_id) -> dict
    Returns: {"classification": str, "confidence": "high"|"medium"|"low",
              "suggested_action": str, "reason": str}
"""

import json
import logging

logger = logging.getLogger("bridge")

_HAIKU_MODEL = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"

CLASSIFICATIONS = {
    "interested":    "Prospect is interested — book a discovery call",
    "not_now":       "Not ready now — add to nurture sequence in 90 days",
    "not_relevant":  "Wrong audience — mark as disqualified in HubSpot",
    "referral":      "Referral received — follow up with the named contact",
    "ooo":           "Out of office — retry in 5 business days",
    "bounce":        "Email bounced — find alternative email address",
    "unsubscribe":   "Unsubscribe request — remove from campaign immediately",
    "unknown":       "Cannot classify — human review required",
}

_SYSTEM_PROMPT = """You classify email replies from prospecting campaigns into one of these categories:
- interested: positive reply, wants more info or a call
- not_now: currently busy or not interested right now
- not_relevant: wrong product, wrong person, or irrelevant to their situation
- referral: asking to speak to someone else or forwarding
- ooo: out of office auto-reply
- bounce: delivery failure or invalid email
- unsubscribe: asking to be removed from emails
- unknown: cannot determine

Respond with a JSON object ONLY (no other text):
{"classification": "LABEL", "confidence": "high|medium|low", "reason": "one sentence"}"""


def _get_bedrock():
    """Return a boto3 Bedrock Runtime client."""
    import boto3
    return boto3.client("bedrock-runtime", region_name="eu-west-1")


async def classify_reply(
    reply_text: str,
    email: str = "",
    campaign_id: str = "",
) -> dict:
    """Classify a reply using Bedrock Haiku.

    Wraps the synchronous boto3 call in asyncio.to_thread.

    Returns:
        {
          "classification": str,
          "confidence": str,
          "reason": str,
          "suggested_action": str,
        }
    """
    import asyncio

    if not reply_text or not reply_text.strip():
        return _fallback("bounce", "Empty reply text — likely a delivery notification")

    # Detect obvious OOO without calling Bedrock
    lower = reply_text.lower()
    if any(p in lower for p in ("out of office", "on leave", "away from", "i am currently away", "auto-reply")):
        return _fallback("ooo", "Auto-detected out-of-office pattern")

    # Detect obvious unsubscribe
    if any(p in lower for p in ("unsubscribe", "remove me", "stop emailing", "please remove")):
        return _fallback("unsubscribe", "Auto-detected unsubscribe request")

    def _call() -> dict:
        try:
            bedrock = _get_bedrock()
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 200,
                "system": _SYSTEM_PROMPT,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"Classify this email reply:\n\n"
                            f"From: {email}\nCampaign: {campaign_id}\n\n"
                            f"{reply_text[:800]}"
                        ),
                    }
                ],
            }
            response = bedrock.invoke_model(
                modelId=_HAIKU_MODEL,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
            result = json.loads(response["body"].read())
            content = result.get("content", [{}])[0].get("text", "{}")
            parsed = json.loads(content)
            label = parsed.get("classification", "unknown")
            confidence = parsed.get("confidence", "medium")
            reason = parsed.get("reason", "")
            return {
                "classification": label if label in CLASSIFICATIONS else "unknown",
                "confidence": confidence,
                "reason": reason,
                "suggested_action": CLASSIFICATIONS.get(label, CLASSIFICATIONS["unknown"]),
            }
        except json.JSONDecodeError as exc:
            logger.warning("reply_classifier_json_error", extra={"error": str(exc)})
            return _fallback("unknown", "Bedrock returned non-JSON response")
        except Exception as exc:
            logger.warning("reply_classifier_bedrock_error", extra={"error": str(exc)})
            return _fallback("unknown", f"Bedrock error: {str(exc)[:80]}")

    return await asyncio.to_thread(_call)


def _fallback(classification: str, reason: str) -> dict:
    return {
        "classification": classification,
        "confidence": "high",
        "reason": reason,
        "suggested_action": CLASSIFICATIONS.get(classification, CLASSIFICATIONS["unknown"]),
    }
