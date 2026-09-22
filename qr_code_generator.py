import argparse
import base64
import json
import os
from pathlib import Path

import qrcode


# Change these values before running the script.
# Examples:
#   QR_LABELS = ["1"]
#   QR_LABELS = ["1", "2"]
#   QR_LABELS = ["all"]
QR_LABELS = ["57"]

# Save generated QR codes to this path.
# Leave it empty to use the desktop.
OUTPUT_ROOT = r"D:\平台二维码\全平台平台二维码"

DEFAULT_OUTPUT_ROOT = Path.home() / "Desktop"

# Output folders. The key is the platform_type from each platform config row.
PLATFORM_FOLDERS = {
    "1": "Ecotopia",
    "2": "Mootune",
    "3": "Console",
    "4": "Stack",
    "5": "Pamigo",
    "12": "PHT",
    "254": "Fulltrace1",
    "7": "Insurance",
}

PAYLOAD_VERSION = 3
LOGIN_API_V1 = "/api/v1/login"
LOGIN_API_V2 = "/api/v2/login"

# Login API is selected by platform family name, not by URL suffix.
# Ecotopia / PHT / GEDP -> v2
# Mootune / Insurance / Fulltrace -> v1
# Pamigo keeps its existing special mapping.
LOGIN_API_FAMILY_RULES = (
    (LOGIN_API_V2, ("ecotopia", "pht", "gedp")),
    (LOGIN_API_V1, ("mootune", "insurance", "fulltrace")),
)
# Platform data only. QR generation rules are defined above and in build_payload().
# Field order:
# (label, [platform name, server/domain address, device address,
#  platform_type, customer_id, app scheme])
#
# Numbering rule:
# - every config row has an explicit sequential label from top to bottom: 1, 2, 3 ...
# - rows marked as discarded/deleted in the Feishu document are intentionally not included
PLATFORM_CONFIGS = [
    # Ecotopia / 01
    ("1", ["Druid Ecotopia user", "https://www.ecotopiago.com/", "device.ecotopiago.com:9010", "1", "1", "ecotopia"]),
    ("2", ["Druid Ecotopia console", "https://www.ecotopiago.com/manager/", "device.ecotopiago.com:9010", "1", "1", "console"]),
    ("3", ["Druid Ecotopia CN user", "https://www.ecotopiago.cn/", "device.ecotopiago.com:9010", "1", "1", "ecotopia"]),
    ("4", ["Druid Ecotopia CN console", "https://www.ecotopiago.cn/manager/", "device.ecotopiago.com:9010", "1", "1", "console"]),
    ("5", ["Asia Ecotopia user", "https://www.ecotopiago.asia/", "device.ecotopiago.asia:9010", "1", "2", "ecotopia"]),
    ("6", ["Asia Ecotopia console", "https://www.ecotopiago.asia/manager/", "device.ecotopiago.asia:9010", "1", "2", "console"]),
    ("7", ["Lotek Ecotopia user", "https://cellwebservice.lotek.com", "device.lotek.com:9010", "1", "3", "ecotopia"]),
    ("8", ["Lotek Ecotopia console", "https://cellwebservice.lotek.com/manager/", "device.lotek.com:9010", "1", "3", "console"]),
    ("9", ["Develop Ecotopia v1 user", "https://ecotopia-v1.develop.druidtech.net/", "device.develop.druidtech.net:19001", "1", "4", "ecotopia"]),
    ("10", ["Develop Ecotopia v1 console", "https://ecotopia-v1.develop.druidtech.net/manager/", "device.develop.druidtech.net:19001", "1", "4", "console"]),
    ("11", ["Develop Ecotopia v2 user", "https://ecotopia-v2.develop.druidtech.net/", "device.develop.druidtech.net:19002", "1", "4", "ecotopia"]),
    ("12", ["Develop Ecotopia v2 console", "https://ecotopia-v2.develop.druidtech.net/manager/", "device.develop.druidtech.net:19002", "1", "4", "console"]),
    ("13", ["Develop Ecotopia v3 user", "https://ecotopia-v3.develop.druidtech.net/", "device.develop.druidtech.net:19003", "1", "4", "ecotopia"]),
    ("14", ["Develop Ecotopia v3 console", "https://ecotopia-v3.develop.druidtech.net/manager/", "device.develop.druidtech.net:19003", "1", "4", "console"]),
    ("15", ["US Ecotopia user", "https://www.ecotopiago.us/", "device.ecotopiago.us:9010", "1", "5", "ecotopia"]),
    ("16", ["US Ecotopia console", "https://www.ecotopiago.us/manager/", "device.ecotopiago.us:9010", "1", "5", "console"]),
    ("17", ["TW Ecotopia user", "https://www.ecotopia.tw/", "device.ecotopia.tw:9010", "1", "6", "ecotopia"]),
    ("18", ["TW Ecotopia console", "https://www.ecotopia.tw/manager/", "device.ecotopia.tw:9010", "1", "6", "console"]),
    ("19", ["Test Ecotopia user", "https://ecotopia.test.druidtech.net/", "device.test.druidtech.net:9010", "1", "253", "ecotopia"]),
    ("20", ["Test Ecotopia console", "https://ecotopia.test.druidtech.net/manager/", "device.test.druidtech.net:9010", "1", "253", "console"]),
    # Mootune / 02
    ("21", ["Druid Mootune user", "https://www.mootunego.com/", "device.mootunego.com:9210", "2", "1", "mootune"]),
    ("22", ["Druid Mootune admin", "https://www.mootunego.com/admin/", "device.mootunego.com:9210", "2", "1", "console"]),
    ("23", ["Druid Mootune console", "https://www.mootunego.com/manager/", "device.mootunego.com:9210", "2", "1", "console"]),
    ("24", ["SA Mootune user", "https://mootune.druidafrica.com/", "mootune-device.druidafrica.com:9210", "2", "2", "mootune"]),
    ("25", ["SA Mootune admin", "https://mootune.druidafrica.com/admin/", "mootune-device.druidafrica.com:9210", "2", "2", "console"]),
    ("26", ["SA Mootune console", "https://mootune.druidafrica.com/manager/", "mootune-device.druidafrica.com:9210", "2", "2", "console"]),
    ("27", ["Develop Mootune v1 user", "https://mootune-v1.develop.druidtech.net/", "device.develop.druidtech.net:19101", "2", "4", "mootune"]),
    ("28", ["Develop Mootune v1 console", "https://mootune-v1.develop.druidtech.net/manager/", "device.develop.druidtech.net:19101", "2", "4", "console"]),
    ("29", ["Develop Mootune v2 user", "https://mootune-v2.develop.druidtech.net/", "device.develop.druidtech.net:19102", "2", "4", "mootune"]),
    ("30", ["Develop Mootune v2 console", "https://mootune-v2.develop.druidtech.net/manager/", "device.develop.druidtech.net:19102", "2", "4", "console"]),
    ("31", ["Develop Mootune v3 user", "https://mootune-v3.develop.druidtech.net/", "device.develop.druidtech.net:19103", "2", "4", "mootune"]),
    ("32", ["Develop Mootune v3 console", "https://mootune-v3.develop.druidtech.net/manager/", "device.develop.druidtech.net:19103", "2", "4", "console"]),
    ("33", ["Test Mootune user", "https://mootune.test.druidtech.net/", "device.test.druidtech.net:9210", "2", "253", "mootune"]),
    ("34", ["Test Mootune console", "https://mootune.test.druidtech.net/manager/", "device.test.druidtech.net:9210", "2", "253", "console"]),
    # Console / 03
    ("35", ["UbiConsole", "https://basic.druidtech.cn/", "api3.druidtech.cn:10100", "3", "1", "console"]),
    ("36", ["BNSJ Console", "https://basic.druidtech.cn/", "api3.druidtech.cn:10100", "3", "3", "console"]),
    ("37", ["Develop UbiConsole v1", "https://ubiconsole-v1.develop.druidtech.net/", "device.develop.druidtech.net:19301", "3", "4", "console"]),
    ("38", ["Develop UbiConsole v2", "https://ubiconsole-v2.develop.druidtech.net/", "device.develop.druidtech.net:19302", "3", "4", "console"]),
    ("39", ["Develop UbiConsole v3", "https://ubiconsole-v3.develop.druidtech.net/", "device.develop.druidtech.net:19303", "3", "4", "console"]),
    # Stack / 04
    ("40", ["Druid Stack current", "https://stack.druidtech.cn/", "device.stack.druidtech.cn:10200", "4", "1", "console"]),
    ("41", ["Druid Stack new", "https://www.stackgo.tech/", "device.stackgo.tech:10200", "4", "1", "console"]),
    ("42", ["Develop Stack v1", "https://stack-v1.develop.druidtech.net/", "device.develop.druidtech.net:19201", "4", "4", "console"]),
    ("43", ["Develop Stack v2", "https://stack-v2.develop.druidtech.net/", "device.develop.druidtech.net:19202", "4", "4", "console"]),
    ("44", ["Develop Stack v3", "https://stack-v3.develop.druidtech.net/", "device.develop.druidtech.net:19203", "4", "4", "console"]),
    ("45", ["Test Stack", "http://stack.test.druidtech.net/", "device.test.druidtech.net:9610", "4", "253", "console"]),
    # Pamigo / 05
    ("46", ["Druid Pamigo user", "https://www.pamigo.tech/", "device.pamigo.tech:9510", "5", "1", "pamigo"]),
    ("47", ["Druid Pamigo console", "https://www.pamigo.tech/manager/", "device.pamigo.tech:9510", "5", "1", "console"]),
    ("48", ["Develop Pamigo v1 console", "https://pamigo-v1.develop.druidtech.net/manager", "device.develop.druidtech.net:19401", "5", "4", "console"]),
    ("49", ["Develop Pamigo v2 console", "https://pamigo-v2.develop.druidtech.net/manager", "device.develop.druidtech.net:19402", "5", "4", "console"]),
    ("50", ["Develop Pamigo v3 console", "https://pamigo-v3.develop.druidtech.net/manager", "device.develop.druidtech.net:19403", "5", "4", "console"]),
    ("51", ["Test Pamigo console", "https://pamigo.test.druidtech.net/manager", "device.test.druidtech.net:9510", "5", "253", "console"]),
    # PHT / 12
    ("52", ["PHT user", "https://www.polarishorizon.cn/", "device.polarishorizon.cn:9011", "12", "1", "pht"]),
    ("53", ["PHT console", "https://www.polarishorizon.cn/manager/", "device.polarishorizon.cn:9011", "12", "1", "console"]),
    ("54", ["Test PHT user", "https://pht.test.druidtech.net", "device.test.druidtech.net:9011", "12", "253", "pht"]),
    ("55", ["Test PHT console", "https://pht.test.druidtech.net/manager/", "device.test.druidtech.net:9011", "12", "253", "console"]),
    # Insurance / 07
    ("56", ["Druid Insurance user", "https://insurance.mootunego.com/", "insurance-device.mootunego.com:9211", "7", "1", "insurance"]),
    ("57", ["Druid Insurance console", "https://insurance.mootunego.com/manager/", "insurance-device.mootunego.com:9211", "7", "1", "console"]),
    # Fulltrace1 / 254
    ("58", ["Fulltrace1 test console", "https://fulltrace1.test.druidtech.net/manager", "device.test.druidtech.net:9212", "254", "253", "console"]),
]


def login_api_for_name(name):
    normalized_name = str(name).strip().lower()
    for login_api, keywords in LOGIN_API_FAMILY_RULES:
        if any(keyword in normalized_name for keyword in keywords):
            return login_api
    return None


def force_type_for(name, platform_type):
    normalized_type = str(platform_type).strip()
    if normalized_type == "5":
        return {"login_api": LOGIN_API_V1, "platform_type": 2}

    login_api = login_api_for_name(name)
    if login_api:
        return {"login_api": login_api, "platform_type": int(normalized_type)}
    return None


def build_payload(config):
    name, server_address, device_address, platform_type, customer_id, _scheme = config
    payload_platform_type = int(str(platform_type).strip())
    payload = {
        "version": PAYLOAD_VERSION,
        "platform_type": payload_platform_type,
        "server_address": server_address.strip(),
        "device_address": device_address.strip(),
    }

    force_type = force_type_for(name, platform_type)
    if force_type:
        payload["coerce_type"] = dict(force_type)
        payload["force_type"] = dict(force_type)

    payload["name"] = name.strip()
    payload["customer_id"] = int(str(customer_id).strip())
    return payload


def build_qr(config):
    scheme = config[5].strip()
    payload = json.dumps(build_payload(config), ensure_ascii=False, separators=(",", ":"))
    data = f"{scheme}://".encode("utf-8") + base64.b64encode(payload.encode("utf-8"))

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=8,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGBA")


def safe_filename(name):
    clean_name = "_".join(name.strip().lower().split())
    clean_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in clean_name)
    return f"{clean_name or 'platform'}.png"


def normalize_labels(labels):
    if isinstance(labels, str):
        labels = labels.replace(",", " ").split()

    result = []
    for label in labels:
        result.extend(str(label).replace(",", " ").split())
    return [label.strip() for label in result if label.strip()]


def selected_configs(labels):
    normalized = normalize_labels(labels)
    if any(label.lower() == "all" for label in normalized):
        return PLATFORM_CONFIGS, True

    by_label = {label: (label, config) for label, config in PLATFORM_CONFIGS}
    result = []
    missing = []
    for label in normalized:
        if label in by_label:
            result.append(by_label[label])
        else:
            missing.append(label)

    if missing:
        valid_labels = ", ".join(label for label, _config in PLATFORM_CONFIGS)
        raise SystemExit(f"Unknown label(s): {', '.join(missing)}\nValid labels: {valid_labels}")
    return result, False


def output_root_for(output_root=None):
    configured_root = output_root or OUTPUT_ROOT
    return Path(configured_root).expanduser() if configured_root else DEFAULT_OUTPUT_ROOT


def category_folder_for(config):
    platform_type = str(config[3]).strip()
    return PLATFORM_FOLDERS.get(platform_type, f"platform_{platform_type or 'unknown'}")


def output_dir_for(config, output_root=None):
    return output_root_for(output_root) / category_folder_for(config)


def open_saved_image(file_path, image):
    if hasattr(os, "startfile"):
        os.startfile(str(file_path))
    else:
        image.show()


def parse_args():
    parser = argparse.ArgumentParser(description="Generate platform QR codes.")
    parser.add_argument(
        "labels",
        nargs="*",
        help="Optional override labels, for example: 1 2. Use all to generate every config.",
    )
    parser.add_argument(
        "-o",
        "--output-root",
        default=None,
        help="Root directory for generated folders. Defaults to the desktop.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Open generated QR images after saving when labels are passed on the command line.",
    )
    parser.add_argument(
        "--no-show",
        action="store_true",
        help="Do not open QR images after saving.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    labels = args.labels or QR_LABELS
    configs, is_all = selected_configs(labels)
    should_show = not args.no_show and (args.show or (not args.labels and not is_all))
    output_root = output_root_for(args.output_root)

    for label, config in configs:
        image = build_qr(config)
        output_dir = output_dir_for(config, output_root)
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / safe_filename(config[0])
        image.save(file_path)
        print(f"{label}: {config[0]} -> {file_path}")
        if should_show:
            open_saved_image(file_path, image)


if __name__ == "__main__":
    main()











