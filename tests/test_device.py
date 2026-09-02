"""
Bloque 8 — Identidad de dispositivos (challenge-response HMAC).

Un dispositivo no autorizado jamás demuestra pertenencia; el secreto
nunca se expone; la revocación es inmediata (HMAC deja de verificar).
"""

from security.device import DeviceRegistry


def test_register_new_device(tmp_path):
    registry = DeviceRegistry(path=str(tmp_path / "devices.json"))

    record = registry.register(
        "phone_yampi",
        display_name="Celular",
        owner_id="owner",
    )

    assert record["status"] == "active"
    assert record["owner"] == "owner"
    assert "secret" not in record


def test_duplicate_device_not_registered(tmp_path):
    registry = DeviceRegistry(path=str(tmp_path / "devices.json"))

    registry.register("phone_yampi", owner_id="owner")

    assert registry.register("phone_yampi", owner_id="owner") is None


def test_challenge_response_verifies(tmp_path):
    registry = DeviceRegistry(path=str(tmp_path / "devices.json"))
    registry.register("phone_yampi", owner_id="owner")

    challenge = registry.challenge()
    signature = registry.sign("phone_yampi", challenge)

    assert signature is not None
    assert registry.verify("phone_yampi", challenge, signature) is True


def test_wrong_signature_fails(tmp_path):
    registry = DeviceRegistry(path=str(tmp_path / "devices.json"))
    registry.register("phone_yampi", owner_id="owner")

    challenge = registry.challenge()

    assert registry.verify("phone_yampi", challenge, "firma-falsa") is False


def test_challenge_changes_each_time(tmp_path):
    registry = DeviceRegistry(path=str(tmp_path / "devices.json"))
    registry.register("phone_yampi", owner_id="owner")

    assert registry.challenge() != registry.challenge()


def test_unregistered_device_cannot_sign(tmp_path):
    registry = DeviceRegistry(path=str(tmp_path / "devices.json"))

    challenge = registry.challenge()

    assert registry.sign("desconocido", challenge) is None
    assert registry.verify("desconocido", challenge, "x") is False


def test_revoked_device_stops_verifying(tmp_path):
    registry = DeviceRegistry(path=str(tmp_path / "devices.json"))
    registry.register("phone_yampi", owner_id="owner")

    challenge = registry.challenge()
    signature = registry.sign("phone_yampi", challenge)
    assert registry.verify("phone_yampi", challenge, signature) is True

    registry.revoke("phone_yampi", "owner", reason="robo")

    assert registry.is_authorized("phone_yampi") is False
    assert registry.verify("phone_yampi", challenge, signature) is False
    assert registry.sign("phone_yampi", challenge) is None


def test_list_never_exposes_secrets(tmp_path):
    registry = DeviceRegistry(path=str(tmp_path / "devices.json"))
    registry.register("phone_yampi", owner_id="owner", display_name="Cel")

    items = registry.list()

    assert len(items) == 1
    item = items[0]

    assert item["device_id"] == "phone_yampi"
    assert "secret" not in item
    assert all("secret" not in key for key in item)


def test_persistence_roundtrip(tmp_path):
    path = str(tmp_path / "devices.json")

    registry = DeviceRegistry(path=path)
    registry.register("tablet", owner_id="owner")

    reloaded = DeviceRegistry(path=path)
    challenge = reloaded.challenge()
    signature = reloaded.sign("tablet", challenge)

    assert reloaded.verify("tablet", challenge, signature) is True


def test_remote_device_cannot_impersonate(tmp_path):
    # El secreto de otro dispositivo no produce una firma válida.
    registry = DeviceRegistry(path=str(tmp_path / "devices.json"))
    registry.register("dispositivo_a", owner_id="owner")
    registry.register("dispositivo_b", owner_id="owner")

    challenge = registry.challenge()
    signature_a = registry.sign("dispositivo_a", challenge)

    # Lo que firma A no sirve presentado como B.
    assert registry.verify("dispositivo_b", challenge, signature_a) is False