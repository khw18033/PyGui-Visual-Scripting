"""
Robot-agnostic mission helper utilities.
Shared by go1.py and ep01.py mission node implementations.
"""
import json
import urllib.request
import urllib.error

# Default schema keys (match mission_config.yaml schema section)
_MISSION_ID_KEYS       = ['mission_id', 'id']
_MISSION_TYPE_KEYS     = ['mission_type', 'type', 'kind']
_MISSION_POST_ACT_KEYS = ['post_action', 'robot_action', 'action']


# ── Type coercions ───────────────────────────────────────────────────────────

def _coerce_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(value)


def _coerce_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _coerce_int(value, default=0):
    try:
        return int(float(value))
    except Exception:
        return default


# ── Signature / container ────────────────────────────────────────────────────

def _mission_signature(value):
    """Deterministic string hash of a mission value for dedup."""
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except Exception:
            return str(value)
    return str(value or '').strip()


def _normalize_mission_container(raw_value):
    """
    Unwrap various JSON envelope patterns and return (payload_dict, signature).
    Handles: bare dict, {mission:...}, {missions:[...]}, list-of-dicts.
    """
    if raw_value is None:
        return {}, ''

    if isinstance(raw_value, str):
        text = raw_value.strip()
        if not text:
            return {}, ''
        try:
            parsed = json.loads(text)
        except Exception:
            return {}, text
        return _normalize_mission_container(parsed)

    if isinstance(raw_value, list):
        signature = _mission_signature(raw_value)
        for item in raw_value:
            if isinstance(item, dict):
                return item, signature
        return {}, signature

    if isinstance(raw_value, dict):
        signature = _mission_signature(raw_value)
        for key in ('mission', 'data', 'payload', 'cmd', 'command'):
            nested = raw_value.get(key)
            if isinstance(nested, dict):
                return nested, signature
        missions = raw_value.get('missions')
        if isinstance(missions, list):
            for item in missions:
                if isinstance(item, dict):
                    return item, signature
        return raw_value, signature

    return {}, _mission_signature(raw_value)


# ── Payload field extraction ─────────────────────────────────────────────────

def _get_mission_value(payload, keys, default=None):
    if not isinstance(payload, dict):
        return default
    for key in (keys or []):
        if key in payload:
            value = payload[key]
            if value not in (None, '', [], {}):
                return value
    return default


def _extract_mission_id(payload, schema=None):
    keys = (schema or {}).get('mission_id_keys', _MISSION_ID_KEYS)
    mission_id = _get_mission_value(payload, keys, '')
    return str(mission_id).strip() if mission_id is not None else ''


def _extract_mission_type(payload, schema=None):
    keys = (schema or {}).get('mission_type_keys', _MISSION_TYPE_KEYS)
    mission_type = _get_mission_value(payload, keys, '')
    return str(mission_type).strip() if mission_type is not None else ''


def _extract_mission_post_action(payload, schema=None):
    if not isinstance(payload, dict):
        return {}
    keys = (schema or {}).get('post_action_keys', _MISSION_POST_ACT_KEYS)
    for key in keys:
        value = payload.get(key)
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            return {'type': value.strip()}
    return {}


# ── HTTP utility ─────────────────────────────────────────────────────────────

def _post_json_payload(url, payload, timeout_sec):
    """POST payload as JSON; returns (status_code, body_str). Raises on HTTP error."""
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=data,
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            charset = resp.headers.get_content_charset() or 'utf-8'
            return resp.status, resp.read().decode(charset, errors='replace').strip()
    except urllib.error.HTTPError as e:
        body = ''
        try:
            body = e.read().decode(
                e.headers.get_content_charset() or 'utf-8', errors='replace'
            ).strip()
        except Exception:
            body = ''
        raise RuntimeError(f'HTTP {e.code}: {body or e.reason}') from e
