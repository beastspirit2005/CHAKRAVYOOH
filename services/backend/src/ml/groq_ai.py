import os
import requests
import json
import logging

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

def enrich_incident_with_groq(payload: str, trigger_type: str, severity: str) -> dict:
    """
    Sends emergency context to Groq (Llama-3) to perform AI triage, severity recommendation, and concise operational summary.
    """
    if not GROQ_API_KEY:
        logger.warning("GROQ_API_KEY not configured. Falling back to rule-based summary.")
        return {
            "summary": f"[{trigger_type}] {payload}",
            "recommended_severity": severity,
            "priority": 4 if severity == "CRITICAL" else 3,
            "action_items": "Dispatch nearest first responder."
        }

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    prompt = f"""
    You are the AI Intelligence Engine for Project Beacon Emergency Mesh.
    Analyze the following emergency distress transmission:
    - Payload Text: "{payload}"
    - Trigger Mechanism: "{trigger_type}"
    - Client Severity: "{severity}"

    Respond ONLY in strict JSON format:
    {{
        "summary": "Concise 1-sentence operational summary for emergency responders",
        "recommended_severity": "CRITICAL, WARNING, or INFO",
        "priority": Integer from 1 (lowest) to 5 (highest),
        "action_items": "Key recommended action for dispatched officers"
    }}
    """

    data = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": "You analyze emergency transmissions and output valid JSON only."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"}
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=5)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        logger.error(f"Groq AI Triage call failed: {e}")
        return {
            "summary": f"[{trigger_type}] {payload}",
            "recommended_severity": severity,
            "priority": 4 if severity == "CRITICAL" else 3,
            "action_items": "Manual verification required."
        }
