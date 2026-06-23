import base64
import shutil
import subprocess

from frappe.tests import IntegrationTestCase

from atlas.atlas.wireguard import (
	ENCODED_KEY_LENGTH,
	KEY_BYTES,
	generate_keypair,
	is_valid_public_key,
	public_key_for,
)


class TestWireGuardKeys(IntegrationTestCase):
	def test_generate_keypair_shape(self):
		keypair = generate_keypair()
		for key in (keypair.private_key, keypair.public_key):
			self.assertEqual(len(key), ENCODED_KEY_LENGTH)
			self.assertEqual(len(base64.b64decode(key, validate=True)), KEY_BYTES)
		# Two halves of a keypair are never equal.
		self.assertNotEqual(keypair.private_key, keypair.public_key)

	def test_generate_keypair_is_random(self):
		self.assertNotEqual(generate_keypair().private_key, generate_keypair().private_key)

	def test_public_key_for_round_trips(self):
		keypair = generate_keypair()
		self.assertEqual(public_key_for(keypair.private_key), keypair.public_key)

	def test_public_key_matches_wg_tool(self):
		# Cross-check our base64/X25519 derivation against WireGuard's own
		# `wg pubkey`, when the tool is on the controller. Skips cleanly without it
		# (still host-free — a local subprocess, no remote host).
		if not shutil.which("wg"):
			self.skipTest("wg not installed on the controller")
		keypair = generate_keypair()
		derived = subprocess.run(
			["wg", "pubkey"],
			input=keypair.private_key,
			capture_output=True,
			text=True,
			check=True,
		).stdout.strip()
		self.assertEqual(derived, keypair.public_key)

	def test_is_valid_public_key_accepts_real_key(self):
		self.assertTrue(is_valid_public_key(generate_keypair().public_key))

	def test_is_valid_public_key_rejects_malformed(self):
		# Wrong length (short / long), a base64 value of the wrong byte count, and
		# a 44-char string with a character outside the base64 alphabet.
		short = base64.standard_b64encode(b"\x00" * 16).decode()  # 24 chars
		for bad in ("", "not a key", "x" * 43, "x" * 45, short, "!" + "A" * 43):
			self.assertFalse(is_valid_public_key(bad), bad)

	def test_is_valid_public_key_rejects_non_string(self):
		# Defensive: the API boundary may hand us a non-str; the isinstance guard
		# rejects it rather than raising.
		for bad in (None, 1234):
			self.assertFalse(is_valid_public_key(bad))  # type: ignore[arg-type]
