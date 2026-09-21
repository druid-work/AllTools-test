#!/usr/bin/env python3
"""Batch-register PHT users.

submit_config.json contains only registration/body-related parameters.
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
class RequestResult:
    index: int
    ok: bool
    status: Optional[int]
    request: Dict[str, Any]
    response: Any
    error: Optional[str] = None


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


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
    required_user = [
        "create_count",
        "batch",
        "fixed_fields",
        "random_enum_fields",
        "runtime",
        "output",
    ]
    missing_user = [key for key in required_user if key not in submit_config]
    if missing_user:
        raise ValueError(f"submit_config.json missing field(s): {', '.join(missing_user)}")
    if not api_config.get("url"):
        raise ValueError("api_config must contain url")
    if not api_config.get("token"):
        raise ValueError("api_config must contain token")
    if int(submit_config["create_count"]) < 1:
        raise ValueError("create_count must be at least 1")
    if int(submit_config["runtime"]["workers"]) < 1:
        raise ValueError("runtime.workers must be at least 1")


def enum_value(config: Dict[str, Any], field: str) -> Any:
    values = config["random_enum_fields"].get(field, [])
    if not values:
        raise ValueError(f"random_enum_fields.{field} cannot be empty")
    return copy.deepcopy(values[secrets.randbelow(len(values))])


def id_card(batch: Dict[str, Any], offset: int) -> str:
    suffix = int(batch["id_card_suffix_start"]) + offset
    width = int(batch["id_card_suffix_width"])
    if suffix >= 10**width:
        raise ValueError("id_card suffix exceeds configured width")
    return f"{batch['id_card_prefix']}{suffix:0{width}d}"


def build_payloads(
    user_config: Dict[str, Any],
    token_config: Dict[str, Any],
    count: int,
) -> list[Dict[str, Any]]:
    if count < 1:
        raise ValueError("create count must be at least 1")

    batch = user_config["batch"]
    fixed = user_config["fixed_fields"]
    payloads: list[Dict[str, Any]] = []
    for offset in range(count):
        number = int(batch["start_number"]) + offset
        phone = str(int(batch["phone_start"]) + offset)
        name = batch["name_template"].format(number=number)
        data = {
            "avatar_image": fixed["avatar_image"],
            "birthday": fixed["birthday"],
            "blood_type": enum_value(user_config, "blood_type"),
            "clothing_size": enum_value(user_config, "clothing_size"),
            "company_name": name,
            "detail_address": copy.deepcopy(fixed["detail_address"]),
            "email": batch["email_template"].format(number=number),
            "emergency_contact": batch["emergency_contact_template"].format(
                number=number
            ),
            "emergency_phone": phone,
            "full_name": name,
            "gender": enum_value(user_config, "gender"),
            "id_card": id_card(batch, offset),
            "id_card_type": fixed["id_card_type"],
            "itra_id": fixed["itra_id"],
            "nationality": fixed["nationality"],
            "nickname": name,
            "phone": phone,
            "pinyin_first_name_upper": fixed["pinyin_first_name_upper"],
            "pinyin_last_name_upper": fixed["pinyin_last_name_upper"],
        }
        payloads.append({"token": token_config["token"], "data": data})
    return payloads


def decode_response(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text[:MAX_RESPONSE_BODY]


def safe_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    copied = copy.deepcopy(payload)
    copied["token"] = "<redacted>"
    return copied


def safe_record(result: RequestResult) -> Dict[str, Any]:
    record = asdict(result)
    if "token" in record["request"]:
        record["request"]["token"] = "<redacted>"
    return record


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


def success_user_record(result: RequestResult) -> Dict[str, Any]:
    data = result.request["data"]
    return {
        "index": result.index,
        "user_id": find_value(result.response, {"user_id", "id", "_id"}),
        "phone": data.get("phone"),
        "name": data.get("full_name") or data.get("nickname"),
        "nickname": data.get("nickname"),
        "id_card": data.get("id_card"),
    }


def write_registered_users(path: Path, users: list[Dict[str, Any]]) -> None:
    users = sorted(users, key=lambda item: item["index"])
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "count": len(users),
        "users": users,
    }
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


def send_one(
    index: int,
    payload: Dict[str, Any],
    api_config: Dict[str, Any],
    timeout: float,
    retries: int,
    retry_delay: float,
) -> RequestResult:
    headers = copy.deepcopy(api_config["headers"])
    headers.setdefault("Accept", "application/json, text/plain, */*")
    headers.setdefault("Content-Type", "application/json")

    last_error = "request failed"
    for attempt in range(retries + 1):
        request = Request(
            api_config["url"],
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                return RequestResult(
                    index=index,
                    ok=200 <= response.status < 300,
                    status=response.status,
                    request=payload,
                    response=decode_response(response.read()),
                )
        except HTTPError as exc:
            response_body = decode_response(exc.read())
            last_error = f"HTTP {exc.code}"
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            if not retryable or attempt >= retries:
                return RequestResult(
                    index=index,
                    ok=False,
                    status=exc.code,
                    request=payload,
                    response=response_body,
                    error=last_error,
                )
        except URLError as exc:
            last_error = f"network error: {exc.reason}"
            if attempt >= retries:
                return RequestResult(
                    index=index,
                    ok=False,
                    status=None,
                    request=payload,
                    response=None,
                    error=last_error,
                )
        except TimeoutError:
            last_error = "request timed out"
            if attempt >= retries:
                return RequestResult(
                    index=index,
                    ok=False,
                    status=None,
                    request=payload,
                    response=None,
                    error=last_error,
                )
        time.sleep(retry_delay * (2**attempt))

    return RequestResult(
        index=index,
        ok=False,
        status=None,
        request=payload,
        response=None,
        error=last_error,
    )


def write_jsonl(handle: Any, value: Any, lock: Lock) -> None:
    with lock:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")
        handle.flush()


def format_failure(result: RequestResult) -> str:
    detail = ""
    if result.response is not None:
        if isinstance(result.response, str):
            detail = result.response
        else:
            detail = json.dumps(result.response, ensure_ascii=False)
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
    count = args.count if args.count is not None else int(submit_config["create_count"])
    workers = (
        args.workers if args.workers is not None else int(runtime["workers"])
    )
    timeout = (
        args.timeout if args.timeout is not None else float(runtime["timeout"])
    )
    retries = (
        args.retries if args.retries is not None else int(runtime["retries"])
    )
    retry_delay = (
        args.retry_delay
        if args.retry_delay is not None
        else float(runtime["retry_delay"])
    )

    if workers < 1 or workers > 128:
        raise ValueError("workers must be between 1 and 128")
    if retries < 0:
        raise ValueError("retries must be zero or greater")
    if timeout <= 0 or retry_delay < 0:
        raise ValueError("timeout must be positive and retry-delay must be zero or greater")

    payloads = build_payloads(submit_config, api_config, count)
    print(f"prepared {len(payloads)} user request(s)")
    print(json.dumps({"first_request": safe_request(payloads[0])}, ensure_ascii=False))

    success_path = resolve_output_path(output_value(output_config, "success_file", "success_log"))
    failure_path = resolve_output_path(output_value(output_config, "failure_file", "failure_log"))
    lock = Lock()
    success_count = 0
    failure_count = 0
    registered_users: list[Dict[str, Any]] = []
    failed_results: list[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                send_one,
                index,
                payload,
                api_config,
                timeout,
                retries,
                retry_delay,
            ): payload
            for index, payload in enumerate(payloads)
        }
        for future in as_completed(futures):
            result = future.result()
            data = result.request["data"]
            label = data.get("phone") or data.get("full_name") or result.index
            if result.ok:
                success_count += 1
                registered_user = success_user_record(result)
                with lock:
                    registered_users.append(registered_user)
                print(f"[{result.index + 1}/{len(payloads)}] OK {label}")
            else:
                failure_count += 1
                with lock:
                    failed_results.append(safe_record(result))
                print(
                    f"[{result.index + 1}/{len(payloads)}] FAILED "
                    f"{label} {format_failure(result)}",
                    file=sys.stderr,
                )

    write_registered_users(success_path, registered_users)
    write_failed_results(failure_path, failed_results)

    print(f"finished: {success_count} succeeded, {failure_count} failed")
    print(f"success file: {success_path}")
    print(f"failure file: {failure_path}")
    return 0 if failure_count == 0 else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Batch-register PHT users.")
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
