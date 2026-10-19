"""パスワード未設定のSwitchBot BotをBLE経由で検出・操作するCLI。

公式仕様: https://github.com/OpenWonderLabs/SwitchBotAPI-BLE/blob/latest/devicetypes/bot.md
動作モードや長押し時間は変更せず、公式アプリで設定された値を使用する。
"""

import argparse
import asyncio
import math
import sys

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.exc import BleakError


# GATTはBLEの通信仕様。UUIDはサービスと送受信用の特性を識別する。
# WRITE/NOTIFYはPC側から見た方向であり、公式仕様のRX/TXとは逆になる。
SERVICE_UUID = "cba20d00-224d-11e6-9fb8-0002a5d5c51b"
WRITE_UUID = "cba20002-224d-11e6-9fb8-0002a5d5c51b"
NOTIFY_UUID = "cba20003-224d-11e6-9fb8-0002a5d5c51b"
# 接続前に受信する広告データのうち、機器種別・状態を格納するサービス。
SERVICE_DATA_UUID = "0000fd3d-0000-1000-8000-00805f9b34fb"

# 0x57は固定ヘッダー、0x01は動作指示、0x02は機器情報の取得。
# 動作指示の末尾は0x00=押下、0x01=ON、0x02=OFF。
COMMANDS = {
    "press": bytes.fromhex("570100"),
    "on": bytes.fromhex("570101"),
    "off": bytes.fromhex("570102"),
    "info": bytes.fromhex("5702"),
}
# 応答の先頭バイト。成功の0x01はchecked_responseで判定する。
# このCLIでは通常使わない暗号化・メッシュ関連の応答も、調査用に残す。
STATUS_MESSAGES = {
    0x02: "Action failed.",
    0x03: "Bot is busy. Try again later.",
    0x04: "Incompatible protocol version.",
    0x05: "Unsupported command. Check press/switch mode in the app.",
    0x06: "Battery is low.",
    0x07: "Bot requires a password. Password-protected Bots are not supported.",
    0x08: "Bot is not encrypted.",
    0x09: "Incorrect password.",
    0x0A: "Unsupported encryption method.",
    0x0B: "Nearby mesh device not found.",
    0x0C: "Network connection failed.",
}


class BotError(Exception):
    """Botの応答異常や、このCLIで検出した通信上の問題を表す。"""


def checked_response(data: bytes) -> bytes:
    """成功ステータスを検証し、ステータスを除いたペイロードを返す。"""
    if not data:
        raise BotError("Empty response from Bot.")
    if data[0] != 0x01:
        message = STATUS_MESSAGES.get(data[0], "Unknown response status.")
        raise BotError(f"Status 0x{data[0]:02X}: {message}")
    return data[1:]


def format_info(payload: bytes) -> str:
    """公式仕様の機器情報ペイロードを、人が読める表示に変換する。"""
    if len(payload) < 12:
        raise BotError(f"Incomplete device information: {payload.hex(' ')}")
    # オフセットはステータス除去後の位置。8番目の上位4ビットが動作モード。
    # 同じバイトの下位ビットは反転設定、9番目は長押し時間を表す。
    mode = "switch" if payload[8] & 0xF0 == 0x10 else "press"
    return (
        f"Battery: {payload[0]}%\nFirmware: {payload[1] / 10:.1f}\n"
        f"Timers: {payload[7]}\nMode: {mode}\n"
        f"Inverse: {bool(payload[8] & 0x01)}\nHold time: {payload[9]} s"
    )


async def exchange(client: BleakClient, command: bytes, timeout: float) -> bytes:
    """接続済みのBotに1回だけ送信し、通知応答を検証して返す。

    timeoutは書き込みと通知待ちにそれぞれ適用する秒数。
    応答がなくても動作済みの場合があるため、自動再送はしない。
    """
    # 通知コールバックの受信結果を、非同期処理で待てるFutureに渡す。
    reply: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()

    def on_notification(sender: BleakGATTCharacteristic, data: bytearray) -> None:
        # 重複通知やタイムアウト後の通知で、完了済みFutureを更新しない。
        if not reply.done():
            reply.set_result(bytes(data))

    # 送信直後の応答を取りこぼさないよう、書き込みより先に通知を購読する。
    await client.start_notify(NOTIFY_UUID, on_notification)
    try:
        characteristic = client.services.get_characteristic(WRITE_UUID)
        if characteristic is None:
            raise BotError("SwitchBot write characteristic not found.")
        # GATT書き込みの応答有無と、Botの実行結果を知らせる通知は別物。
        # 対応する書き込み方式を選び、どちらの場合も通知で成功を確認する。
        if "write-without-response" in characteristic.properties:
            write_response = False
        elif "write" in characteristic.properties:
            write_response = True
        else:
            raise BotError("SwitchBot characteristic does not support writing.")
        print(f"TX: {command.hex(' ')}")
        try:
            await asyncio.wait_for(
                client.write_gatt_char(characteristic, command, response=write_response),
                timeout=timeout,
            )
            data = await asyncio.wait_for(reply, timeout=timeout)
        except TimeoutError as error:
            raise BotError(
                "No confirmed response. The action may already have executed; "
                "check the Bot before retrying."
            ) from error
        print(f"RX: {data.hex(' ')}")
        return checked_response(data)
    finally:
        # 例外時も購読を解除する。切断済みならBleak側で解除される。
        if client.is_connected:
            await client.stop_notify(NOTIFY_UUID)


async def scan(timeout: float, show_all: bool) -> None:
    """広告データからBotを検出して表示する。接続やアーム操作は行わない。"""
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    count = 0
    for device, advertisement in sorted(devices.values(), key=lambda item: item[0].address):
        data = advertisement.service_data.get(SERVICE_DATA_UUID, b"")
        # 先頭バイトのbit 7は暗号化フラグ。下位7ビットの0x48がBotを表す。
        is_bot = bool(data) and data[0] & 0x7F == 0x48
        name = advertisement.local_name or device.name or "(unnamed)"
        # 旧ファームウェアは名前とサービスUUIDの組み合わせで判定する。
        legacy_bot = (
            name.lower() in {"bot", "wohand"}
            and SERVICE_UUID in advertisement.service_uuids
        )
        if not is_bot and not legacy_bot and not show_all:
            continue
        count += 1
        details = ""
        if is_bot and len(data) >= 3:
            # 状態バイトのbit 7はモード、bit 6はOFF。電池残量は下位7ビット。
            mode = "switch" if data[1] & 0x80 else "press"
            details = (
                f" battery={data[2] & 0x7F}% mode={mode}"
                f" encrypted={bool(data[0] & 0x80)}"
            )
            if mode == "switch":
                details += " state=off" if data[1] & 0x40 else " state=on"
        print(f"{device.address}  {name}  RSSI={advertisement.rssi}{details}")
    if count == 0:
        print("No Bot found. Check Bluetooth, move closer, or try scan --all --timeout 15.")


async def run(args: argparse.Namespace) -> None:
    """CLI引数に従って検出または操作を実行し、結果を表示する。"""
    if args.command == "scan":
        await scan(args.timeout, args.all)
        return
    device = await BleakScanner.find_device_by_address(args.address, timeout=args.scan_timeout)
    if device is None:
        raise BotError(f"Device not found: {args.address}. Run scan and check the address.")
    # 検出で得たBLEDeviceを渡し、暗黙の再スキャンを避ける。
    # async withにより、正常終了時も例外時も切断する。
    async with BleakClient(device, timeout=args.connect_timeout) as client:
        payload = await exchange(client, COMMANDS[args.command], args.response_timeout)
    print(format_info(payload) if args.command == "info" else f"OK: {args.command}")


def positive_seconds(value: str) -> float:
    """タイムアウト引数を、正の有限な秒数として検証する。"""
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("Must be a finite number greater than zero.")
    return seconds


def make_parser() -> argparse.ArgumentParser:
    """検出用と操作用のサブコマンド、および必須引数を定義する。"""
    parser = argparse.ArgumentParser(description="Control a password-free SwitchBot Bot over BLE.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan_parser = subparsers.add_parser("scan", help="List nearby Bots without connecting.")
    scan_parser.add_argument("--timeout", type=positive_seconds, default=10.0)
    scan_parser.add_argument("--all", action="store_true", help="Show all BLE devices.")
    for command in COMMANDS:
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--address", required=True, help="Address reported by scan.")
        command_parser.add_argument("--scan-timeout", type=positive_seconds, default=10.0)
        command_parser.add_argument("--connect-timeout", type=positive_seconds, default=20.0)
        command_parser.add_argument("--response-timeout", type=positive_seconds, default=15.0)
    return parser


def main() -> int:
    """CLIを実行し、成功は0、エラーは1、ユーザー中断は130を返す。"""
    args = make_parser().parse_args()
    try:
        asyncio.run(run(args))
    except (BotError, BleakError, OSError, TimeoutError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())