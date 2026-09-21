#!/usr/bin/env python3
"""Batch-sign users up for a PHT event.

submit_config.json contains only signup/body-related parameters.
api_config.json contains URL/auth/headers and may be replaced by raw copied curl.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import secrets
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_DIR = Path(__file__).resolve().parent
SUBMIT_CONFIG_FILE = BASE_DIR / "submit_config.json"
API_CONFIG_FILE = BASE_DIR / "api_config.json"
MAX_RESPONSE_BODY = 4096


@dataclass(frozen=True)
class SignupUser:
    index: int
    user_id: str
    phone: str
    name: str = ""


@dataclass(frozen=True)
class RequestResult:
    index: int
    ok: bool
    status: Optional[int]
    request: Dict[str, Any]
    response: Any
    error: Optional[str] = None


def clean_curl_text(value: str) -> str:
    value = value.strip()
    value = value.replace("\\\r\n", " ").replace("\\\n", " ")
    value = value.replace("\\_", "_").replace("\\@", "@").replace("\\&", "&")
    value = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\2", value)
    return value


def read_json_or_raw(path: Path) -> Any:
    text = path.read_text(encoding="utf-8").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"curl": text}


def extract_quoted_option(curl: str, option: str) -> Optional[str]:
    match = re.search(rf"{re.escape(option)}\s+(['\"])(.*?)\1", curl, re.DOTALL)
    if match:
        return match.group(2).strip()
    return None


def extract_headers(curl: str) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for match in re.finditer(r"-H\s+(['\"])(.*?)\1", curl, re.DOTALL):
        raw = match.group(2).strip()
        if ":" not in raw:
            continue
        name, value = raw.split(":", 1)
        headers[name.strip()] = value.strip()
    cookie = extract_quoted_option(curl, "-b")
    if cookie:
        headers["Cookie"] = cookie
    return headers


def extract_data_raw(curl: str) -> Dict[str, Any]:
    raw = extract_quoted_option(curl, "--data-raw")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"failed to parse curl --data-raw JSON: {exc}") from exc


def parse_curl_api(curl: str) -> Dict[str, Any]:
    cleaned = clean_curl_text(curl)
    url = extract_quoted_option(cleaned, "--url")
    if not url:
        raise ValueError("api_config curl is missing --url")
    data = extract_data_raw(cleaned)
    token = data.get("token")
    if not token:
        raise ValueError("api_config curl --data-raw is missing token")
    return {"url": url, "token": token, "headers": extract_headers(cleaned)}


def load_submit_config(path: Path) -> Dict[str, Any]:
    value = read_json_or_raw(path)
    if not isinstance(value, dict):
        raise ValueError("submit_config.json must contain a JSON object")
    return value


def load_api_config(path: Path) -> Dict[str, Any]:
    value = read_json_or_raw(path)
    if not isinstance(value, dict):
        raise ValueError("api_config.json must contain a JSON object or raw curl text")
    curl = str(value.get("curl", "")).strip()
    if curl:
        parsed = parse_curl_api(curl)
        for key, item in value.items():
            if key not in {"curl", "url", "token", "headers"}:
                parsed[key] = item
        return parsed
    required = ["url", "token", "headers"]
    missing = [key for key in required if key not in value]
    if missing:
        raise ValueError(f"api_config.json missing field(s): {', '.join(missing)}")
    return value


def validate_configs(submit_config: Dict[str, Any], api_config: Dict[str, Any]) -> None:
    required = ["event_id", "levels", "level_assignment", "users", "runner_number", "runtime", "output"]
    missing = [key for key in required if key not in submit_config]
    if missing:
        raise ValueError(f"submit_config.json missing field(s): {', '.join(missing)}")
    if not api_config.get("url"):
        raise ValueError("api_config must contain url")
    if not api_config.get("token"):
        raise ValueError("api_config must contain token")
    if not submit_config["levels"]:
        raise ValueError("levels cannot be empty")
    if submit_config["level_assignment"] not in {"round_robin", "random", "first", "fixed_counts"}:
        raise ValueError("level_assignment must be round_robin, random, first, or fixed_counts")


def resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return BASE_DIR / path


def find_value(value: Any, keys: set[str]) -> Optional[Any]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and item not in (None, ""):
                return item
        for item in value.values():
            found = find_value(item, keys)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_value(item, keys)
            if found not in (None, ""):
                return found
    return None


def load_users_from_success_log(path: Path) -> list[SignupUser]:
    users: list[SignupUser] = []
    if not path.exists():
        return users
    with path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if not line.strip():
                continue
            record = json.loads(line)
            request_data = record.get("request", {}).get("data", {})
            response = record.get("response", {})
            user_id = find_value(response, {"user_id", "id", "_id"})
            phone = request_data.get("phone") or find_value(response, {"phone"})
            name = request_data.get("full_name") or request_data.get("nickname") or ""
            if user_id and phone:
                users.append(SignupUser(index=index, user_id=str(user_id), phone=str(phone), name=str(name)))
    return users


def load_users_from_users_json(path: Path) -> list[SignupUser]:
    if not path.exists():
        return []
    value = load_submit_config(path)
    items = value.get("users", [])
    if not isinstance(items, list):
        raise ValueError(f"{path} field users must be a list")
    users: list[SignupUser] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        user_id = item.get("user_id") or item.get("id") or item.get("_id")
        phone = item.get("phone")
        if not user_id or not phone:
            continue
        users.append(
            SignupUser(
                index=index,
                user_id=str(user_id),
                phone=str(phone),
                name=str(item.get("name") or item.get("full_name") or item.get("nickname") or ""),
            )
        )
    return users


def load_manual_users(items: list[Dict[str, Any]], start_index: int = 0) -> list[SignupUser]:
    users: list[SignupUser] = []
    for offset, item in enumerate(items):
        user_id = item.get("user_id") or item.get("id") or item.get("_id")
        phone = item.get("phone")
        if not user_id or not phone:
            raise ValueError("manual_users item must contain user_id and phone")
        users.append(SignupUser(index=start_index + offset, user_id=str(user_id), phone=str(phone), name=str(item.get("name") or item.get("full_name") or item.get("nickname") or "")))
    return users


def load_users(config: Dict[str, Any]) -> list[SignupUser]:
    users_config = config["users"]
    users: list[SignupUser] = []
    users_json = str(
        users_config.get("register_success_file") or users_config.get("users_json") or ""
    ).strip()
    if users_json:
        users.extend(load_users_from_users_json(resolve_path(users_json)))
    success_log = str(users_config.get("success_log", "")).strip()
    if success_log:
        users.extend(load_users_from_success_log(resolve_path(success_log)))
    manual_users = users_config.get("manual_users", [])
    if manual_users:
        users.extend(load_manual_users(manual_users, start_index=len(users)))

    deduped: list[SignupUser] = []
    seen: set[str] = set()
    excluded = {str(item) for item in users_config.get("exclude_user_ids", [])}
    for user in users:
        if user.user_id in excluded:
            continue
        if user.user_id in seen:
            continue
        seen.add(user.user_id)
        deduped.append(SignupUser(index=len(deduped), user_id=user.user_id, phone=user.phone, name=user.name))
    limit = users_config.get("limit")
    if limit not in (None, 0):
        deduped = deduped[: int(limit)]
    if not deduped:
        raise ValueError("no users found; fill users.register_success_file, users.manual_users, or users.success_log")
    return deduped


def choose_level(config: Dict[str, Any], index: int) -> Dict[str, Any]:
    levels = config["levels"]
    mode = config["level_assignment"]
    if mode == "first":
        return levels[0]
    if mode == "random":
        return levels[secrets.randbelow(len(levels))]
    return levels[index % len(levels)]


def find_level(config: Dict[str, Any], level_ref: Dict[str, Any]) -> Dict[str, Any]:
    level_id = level_ref.get("level_id") or level_ref.get("id")
    level_name = level_ref.get("name")
    for level in config["levels"]:
        if level_id and level.get("id") == level_id:
            return level
        if level_name and level.get("name") == level_name:
            return level
    raise ValueError(f"level not found in levels: {level_ref}")


def fixed_count_levels(config: Dict[str, Any], user_count: int) -> list[Dict[str, Any]]:
    plan = config.get("level_distribution", [])
    if not plan:
        raise ValueError("level_distribution is required when level_assignment is fixed_counts")

    assigned: list[Dict[str, Any]] = []
    remaining_index: Optional[int] = None
    for index, item in enumerate(plan):
        count = item.get("count")
        if count == "remaining":
            if remaining_index is not None:
                raise ValueError("only one level_distribution item can use count=remaining")
            remaining_index = index
            continue
        level = find_level(config, item)
        assigned.extend([level] * int(count))

    if remaining_index is not None:
        remaining = user_count - len(assigned)
        if remaining < 0:
            raise ValueError("level_distribution fixed counts exceed user count")
        level = find_level(config, plan[remaining_index])
        assigned.extend([level] * remaining)

    if len(assigned) < user_count:
        raise ValueError("level_distribution count is less than user count")
    return assigned[:user_count]


def runner_number(user: SignupUser, config: Dict[str, Any]) -> str:
    runner_config = config["runner_number"]
    mode = runner_config.get("mode", "phone_last4")
    if mode == "phone_last4":
        width = int(runner_config.get("width", 4))
        return user.phone[-width:].zfill(width)
    if mode == "sequence":
        start = int(runner_config.get("start", 1))
        width = int(runner_config.get("width", 4))
        return f"{start + user.index:0{width}d}"
    raise ValueError(f"unsupported runner_number mode: {mode}")


def build_payloads(config: Dict[str, Any], api_config: Dict[str, Any]) -> list[Dict[str, Any]]:
    payloads: list[Dict[str, Any]] = []
    users = load_users(config)
    fixed_levels = fixed_count_levels(config, len(users)) if config["level_assignment"] == "fixed_counts" else []
    for index, user in enumerate(users):
        level = fixed_levels[index] if fixed_levels else choose_level(config, index)
        payloads.append({"token": api_config["token"], "data": {"event_id": config["event_id"], "level_id": level["id"], "user_id": user.user_id, "runner_number": runner_number(user, config)}, "_meta": {"phone": user.phone, "name": user.name, "level_name": level.get("name", "")}})
    return payloads


def request_body(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"token": payload["token"], "data": payload["data"]}


def decode_response(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text[:MAX_RESPONSE_BODY]


def business_ok(response: Any) -> bool:
    if not isinstance(response, dict):
        return True
    code = response.get("code")
    if code not in (None, 0, 200, "0", "200"):
        return False
    data = response.get("data")
    if isinstance(data, dict):
        result = data.get("result")
        if result not in (None, 0, "0"):
            return False
    return True


def safe_record(result: RequestResult) -> Dict[str, Any]:
    record = asdict(result)
    if "token" in record["request"]:
        record["request"]["token"] = "<redacted>"
    return record


def signup_assignment(result: RequestResult, meta: Dict[str, Any]) -> Dict[str, Any]:
    data = result.request["data"]
    return {
        "index": result.index,
        "event_id": data["event_id"],
        "level_id": data["level_id"],
        "level_name": meta.get("level_name", ""),
        "user_id": data["user_id"],
        "runner_number": data["runner_number"],
        "phone": meta.get("phone", ""),
        "name": meta.get("name", ""),
    }


def write_assignments(path: Path, assignments: list[Dict[str, Any]]) -> None:
    assignments = sorted(assignments, key=lambda item: item["index"])
    grouped: Dict[str, Any] = {}
    event_id = assignments[0]["event_id"] if assignments else ""
    for item in assignments:
        level_id = item["level_id"]
        level = grouped.setdefault(
            level_id,
            {
                "level_id": level_id,
                "level_name": item.get("level_name", ""),
                "count": 0,
                "users": [],
            },
        )
        level["count"] += 1
        level["users"].append(
            {
                "user_id": item["user_id"],
                "phone": item.get("phone", ""),
                "name": item.get("name", ""),
                "runner_number": item["runner_number"],
                "event_id": item["event_id"],
                "level_id": level_id,
            }
        )
    payload = {
        "event_id": event_id,
        "count": len(assignments),
        "levels": grouped,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_failed_results(path: Path, failures: list[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "count": len(failures),
        "failures": failures,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def output_value(output_config: Dict[str, Any], primary: str, fallback: str) -> str:
    value = output_config.get(primary) or output_config.get(fallback)
    if not value:
        raise ValueError(f"output.{primary} is required")
    return str(value)


def send_one(index: int, payload: Dict[str, Any], api_config: Dict[str, Any], timeout: float, retries: int, retry_delay: float) -> RequestResult:
    body = request_body(payload)
    headers = copy.deepcopy(api_config["headers"])
    headers.setdefault("Accept", "application/json, text/plain, */*")
    headers.setdefault("Content-Type", "application/json")
    last_error = "request failed"
    for attempt in range(retries + 1):
        request = Request(api_config["url"], data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
        try:
            with urlopen(request, timeout=timeout) as response:
                response_body = decode_response(response.read())
                return RequestResult(index=index, ok=200 <= response.status < 300 and business_ok(response_body), status=response.status, request=body, response=response_body)
        except HTTPError as exc:
            response_body = decode_response(exc.read())
            last_error = f"HTTP {exc.code}"
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            if not retryable or attempt >= retries:
                return RequestResult(index=index, ok=False, status=exc.code, request=body, response=response_body, error=last_error)
        except URLError as exc:
            last_error = f"network error: {exc.reason}"
            if attempt >= retries:
                return RequestResult(index=index, ok=False, status=None, request=body, response=None, error=last_error)
        except TimeoutError:
            last_error = "request timed out"
            if attempt >= retries:
                return RequestResult(index=index, ok=False, status=None, request=body, response=None, error=last_error)
        time.sleep(retry_delay * (2**attempt))
    return RequestResult(index=index, ok=False, status=None, request=body, response=None, error=last_error)


def write_jsonl(handle: Any, value: Any, lock: Lock) -> None:
    with lock:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")
        handle.flush()


def format_failure(result: RequestResult) -> str:
    detail = ""
    if result.response is not None:
        detail = result.response if isinstance(result.response, str) else json.dumps(result.response, ensure_ascii=False)
        detail = f" response={detail[:MAX_RESPONSE_BODY]}"
    return f"{result.error or result.status}{detail}"


def resolve_output_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return BASE_DIR / path


def run(args: argparse.Namespace) -> int:
    submit_config = load_submit_config(Path(args.submit_config))
    api_config = load_api_config(Path(args.api_config))
    validate_configs(submit_config, api_config)
    runtime = submit_config["runtime"]
    output_config = submit_config["output"]
    workers = args.workers if args.workers is not None else int(runtime["workers"])
    timeout = args.timeout if args.timeout is not None else float(runtime["timeout"])
    retries = args.retries if args.retries is not None else int(runtime["retries"])
    retry_delay = args.retry_delay if args.retry_delay is not None else float(runtime["retry_delay"])
    payloads = build_payloads(submit_config, api_config)
    if args.count is not None:
        payloads = payloads[: args.count]
    if workers < 1 or workers > 128:
        raise ValueError("workers must be between 1 and 128")
    if retries < 0:
        raise ValueError("retries must be zero or greater")
    if timeout <= 0 or retry_delay < 0:
        raise ValueError("timeout must be positive and retry-delay must be zero or greater")

    print(f"prepared {len(payloads)} signup request(s)")
    preview = copy.deepcopy(payloads[0])
    preview["token"] = "<redacted>"
    print(json.dumps({"first_request": request_body(preview), "meta": preview["_meta"]}, ensure_ascii=False))
    success_path = resolve_output_path(output_value(output_config, "success_file", "success_log"))
    failure_path = resolve_output_path(output_value(output_config, "failure_file", "failure_log"))
    lock = Lock()
    success_count = 0
    failure_count = 0
    assignments: list[Dict[str, Any]] = []
    failed_results: list[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(send_one, index, payload, api_config, timeout, retries, retry_delay): payload for index, payload in enumerate(payloads)}
        for future in as_completed(futures):
            payload = futures[future]
            result = future.result()
            label = f"{payload['_meta'].get('phone', '')} {payload['_meta'].get('level_name', '')}"
            if result.ok:
                success_count += 1
                assignment = signup_assignment(result, payload["_meta"])
                with lock:
                    assignments.append(assignment)
                print(f"[{result.index + 1}/{len(payloads)}] OK {label}")
            else:
                failure_count += 1
                with lock:
                    failed_results.append({**safe_record(result), "meta": payload["_meta"]})
                print(f"[{result.index + 1}/{len(payloads)}] FAILED {label} {format_failure(result)}", file=sys.stderr)
    write_assignments(success_path, assignments)
    write_failed_results(failure_path, failed_results)
    print(f"finished: {success_count} succeeded, {failure_count} failed")
    print(f"success file: {success_path}")
    print(f"failure file: {failure_path}")
    return 0 if failure_count == 0 else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Batch-sign users up for a PHT event.")
    parser.add_argument("--submit-config", default=str(SUBMIT_CONFIG_FILE))
    parser.add_argument("--api-config", default=str(API_CONFIG_FILE))
    parser.add_argument("--count", type=int, default=None, help="temporary count override")
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--retries", type=int, default=None)
    parser.add_argument("--retry-delay", type=float, default=None)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
