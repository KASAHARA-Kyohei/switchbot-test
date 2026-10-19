import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import switchbot_bot as bot


class ProtocolTests(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(bot.COMMANDS, {
            "press": bytes.fromhex("570100"), "on": bytes.fromhex("570101"),
            "off": bytes.fromhex("570102"), "info": bytes.fromhex("5702"),
        })

    def test_response_status(self):
        self.assertEqual(bot.checked_response(bytes.fromhex("01ff00")), b"\xff\x00")
        for data in (b"", b"\x03", b"\x05", b"\x07", b"\xff"):
            with self.subTest(data=data), self.assertRaises(bot.BotError):
                bot.checked_response(data)

    def test_official_info_example(self):
        result = bot.format_info(bot.checked_response(bytes.fromhex("01642c64000000a10000004800")))
        self.assertIn("Battery: 100%", result)
        self.assertIn("Firmware: 4.4", result)
        self.assertIn("Mode: press", result)
        with self.assertRaises(bot.BotError):
            bot.format_info(b"\x64")

    def test_timeout_validation(self):
        self.assertEqual(bot.positive_seconds("1.5"), 1.5)
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(value=value), self.assertRaises(bot.argparse.ArgumentTypeError):
                bot.positive_seconds(value)


class BleTests(unittest.IsolatedAsyncioTestCase):
    def make_client(self, properties=None, response=b"\x01\xff\x00"):
        characteristic = SimpleNamespace(properties=properties or ["write-without-response"])
        client = SimpleNamespace(
            services=SimpleNamespace(get_characteristic=Mock(return_value=characteristic)),
            start_notify=AsyncMock(), stop_notify=AsyncMock(),
            write_gatt_char=AsyncMock(), is_connected=True,
        )

        async def write(*args, **kwargs):
            client.start_notify.assert_awaited_once()
            if response is not None:
                callback = client.start_notify.call_args.args[1]
                callback(characteristic, bytearray(response))

        client.write_gatt_char.side_effect = write
        return client, characteristic

    async def test_notify_before_write_and_cleanup(self):
        for properties, write_response in ((["write-without-response"], False), (["write"], True)):
            client, characteristic = self.make_client(properties)
            with contextlib.redirect_stdout(io.StringIO()):
                payload = await bot.exchange(client, bot.COMMANDS["press"], 1)
            self.assertEqual(payload, b"\xff\x00")
            self.assertEqual(client.start_notify.call_args.args[0], bot.NOTIFY_UUID)
            client.services.get_characteristic.assert_called_once_with(bot.WRITE_UUID)
            client.write_gatt_char.assert_awaited_once_with(
                characteristic, bytes.fromhex("570100"), response=write_response,
            )
            client.stop_notify.assert_awaited_once_with(bot.NOTIFY_UUID)

    async def test_timeout_does_not_retry(self):
        client, characteristic = self.make_client(response=None)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(bot.BotError, "may already"):
            await bot.exchange(client, bot.COMMANDS["press"], 0.01)
        self.assertEqual(client.write_gatt_char.await_count, 1)
        client.stop_notify.assert_awaited_once()

    async def test_password_error_and_cleanup(self):
        client, characteristic = self.make_client(response=b"\x07")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(bot.BotError, "password"):
            await bot.exchange(client, bot.COMMANDS["press"], 1)
        client.stop_notify.assert_awaited_once()

    async def test_missing_write_characteristic(self):
        client, characteristic = self.make_client()
        client.services.get_characteristic.return_value = None
        with self.assertRaisesRegex(bot.BotError, "not found"):
            await bot.exchange(client, bot.COMMANDS["press"], 1)
        client.write_gatt_char.assert_not_awaited()
        client.stop_notify.assert_awaited_once()

    async def test_unknown_address_does_not_connect(self):
        args = bot.make_parser().parse_args(["press", "--address", "AA:BB:CC:DD:EE:FF"])
        with patch.object(bot.BleakScanner, "find_device_by_address", new=AsyncMock(return_value=None)), \
                patch.object(bot, "BleakClient") as client:
            with self.assertRaisesRegex(bot.BotError, "Device not found"):
                await bot.run(args)
        client.assert_not_called()

    async def test_scan_filters_other_device_types(self):
        device = SimpleNamespace(address="AA:BB:CC:DD:EE:FF", name=None)
        advertisement = SimpleNamespace(
            service_data={bot.SERVICE_DATA_UUID: bytes.fromhex("c88064")},
            local_name=None, service_uuids=[], rssi=-50,
        )
        output = io.StringIO()
        with patch.object(bot.BleakScanner, "discover", new=AsyncMock(return_value={device.address: (device, advertisement)})), \
                contextlib.redirect_stdout(output):
            await bot.scan(1, False)
        self.assertIn(device.address, output.getvalue())
        self.assertIn("battery=100% mode=switch encrypted=True state=on", output.getvalue())
        advertisement.service_data[bot.SERVICE_DATA_UUID] = bytes.fromhex("540064")
        output = io.StringIO()
        with patch.object(bot.BleakScanner, "discover", new=AsyncMock(return_value={device.address: (device, advertisement)})), \
                contextlib.redirect_stdout(output):
            await bot.scan(1, False)
        self.assertNotIn(device.address, output.getvalue())


if __name__ == "__main__":
    unittest.main()