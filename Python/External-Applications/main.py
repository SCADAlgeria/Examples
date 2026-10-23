"""
Minimal SCADALGERIA External MQTT Tool

The program:
    1. Connects to SCADALGERIA.
    2. Authenticates using the SCADALGERIA MQTT challenge.
    3. Sends one sine-wave telemetry value every second.
    4. Receives commands from SCADALGERIA.
    5. Executes supported commands.
    6. Sends command results back to SCADALGERIA.

Default sine wave:
    center    = 50
    amplitude = 40
    period    = 300 seconds

Therefore:
    minimum = 10
    maximum = 90
    one complete wave = 5 minutes
"""


# =====================================================================
# STEP 1 OF 9 — IMPORTS
#
# These are the only libraries needed by this example.
#
# math:
#     Generates the sine wave.
#
# time / os / uuid / base64 / hashlib / hmac / struct:
#     Handle timing, identity, authentication, sequence numbers and crypto.
#
# msgpack:
#     SCADALGERIA uses MessagePack for MQTT payloads.
#
# paho.mqtt:
#     MQTT client.
#
# cryptography:
#     HKDF-SHA256 and AES-256-GCM required by SCADALGERIA.
# =====================================================================

import base64
import hashlib
import hmac
import math
import os
import struct
import time
import uuid

import msgpack
import paho.mqtt.client as mqtt

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


# =====================================================================
# STEP 2 OF 9 — VALUES FROM SCADALGERIA
#
# Put all information obtained from SCADALGERIA here.
#
# MQTT_HOST:
#     IP address or hostname where the SCADALGERIA Engine MQTT listener
#     is exposed.
#
# MQTT_PORT:
#     MQTT TCP port.
#
# TENANT_ID:
#     Your SCADALGERIA tenant.
#
# PROJECT_ID:
#     The project containing this source.
#
# SOURCE_ID:
#     The source/connection configured in SCADALGERIA.
#
# API_KEY_ID:
#     ID of the API key assigned to this MQTT source.
#
# API_KEY_SECRET:
#     The one-time API-key Token issued by SCADALGERIA.
#
# TAG_ADDRESS:
#     Address configured for this source in SCADALGERIA.
# =====================================================================

MQTT_HOST = "your-vps-ip-or-domain"
MQTT_PORT = 1883

TENANT_ID = "00000000-0000-0000-0000-000000000001"
PROJECT_ID = "00000000-0000-0000-0000-000000000002"

SOURCE_ID = "python-sine-generator"

API_KEY_ID = "00000000-0000-0000-0000-000000000003"
API_KEY_SECRET = "your-api-key-token"

TAG_ADDRESS = "sine_wave"


# =====================================================================
# STEP 3 OF 9 — LOCAL APPLICATION SETTINGS
#
# These values belong to this Python application rather than SCADALGERIA.
#
# A value is sent every 1 second.
#
# A period of 300 seconds means:
#
#     300 samples / cycle
#     5 minutes / complete sine wave
#
# With:
#
#     center    = 50
#     amplitude = 40
#
# the generated value moves smoothly between:
#
#     10 and 90
#
# These three settings can also be changed remotely through SCADALGERIA
# commands.
# =====================================================================

SEND_EVERY_SECONDS = 1.0

sine_period = 300.0
sine_center = 50.0
sine_amplitude = 40.0


# =====================================================================
# STEP 4 OF 9 — MQTT IDENTITY AND TOPICS
#
# SCADALGERIA derives the MQTT identity from:
#
#     tenant
#     API key
#     source
#
# The source ID is Base64URL encoded in the MQTT username.
#
# The external tool uses five MQTT topics:
#
#     auth/challenge
#         SCADALGERIA -> client authentication challenge
#
#     auth/response
#         client -> SCADALGERIA authentication proof
#
#     tags
#         client -> SCADALGERIA telemetry
#
#     commands
#         SCADALGERIA -> client commands
#
#     commands/result
#         client -> SCADALGERIA command execution result
# =====================================================================

CLIENT_ID = f"{SOURCE_ID}-{uuid.uuid4().hex[:8]}"

encoded_source = base64.urlsafe_b64encode(
    SOURCE_ID.encode("utf-8")
).decode("ascii").rstrip("=")

MQTT_USERNAME = (
    f"v1/"
    f"{TENANT_ID}/"
    f"{API_KEY_ID}/"
    f"{encoded_source}"
)

AUTH_CHALLENGE_TOPIC = (
    f"scada/{TENANT_ID}/{API_KEY_ID}/"
    f"{CLIENT_ID}/auth/challenge"
)

AUTH_RESPONSE_TOPIC = (
    f"scada/{TENANT_ID}/{API_KEY_ID}/"
    f"{CLIENT_ID}/auth/response"
)

TELEMETRY_TOPIC = (
    f"scada/{TENANT_ID}/{PROJECT_ID}/"
    f"{SOURCE_ID}/tags"
)

COMMAND_TOPIC = (
    f"scada/{TENANT_ID}/{PROJECT_ID}/"
    f"{SOURCE_ID}/commands"
)

COMMAND_RESULT_TOPIC = (
    f"scada/{TENANT_ID}/{PROJECT_ID}/"
    f"{SOURCE_ID}/commands/result"
)


# Session encryption key.
# It becomes available after authentication.
session_key = None

# Used to make each AES-GCM nonce unique inside this MQTT session.
nonce_counter = 0

# Last outgoing SCADALGERIA replay sequence.
last_sequence = 0


# =====================================================================
# STEP 5 OF 9 — AUTHENTICATION
#
# SCADALGERIA does NOT use the API key secret as an MQTT password.
#
# Authentication works like this:
#
#     connect
#         ↓
#     receive server nonce
#         ↓
#     generate client nonce
#         ↓
#     HMAC-SHA256 proof
#         ↓
#     send auth response
#         ↓
#     derive AES session key with HKDF-SHA256
#
# After authentication, the same session key protects telemetry,
# commands and command results.
# =====================================================================


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code.is_failure:
        print("MQTT connection failed:", reason_code)
        return

    print("Connected to SCADALGERIA")

    # Before authentication, this is the only subscription SCADALGERIA permits.
    client.subscribe(
        AUTH_CHALLENGE_TOPIC,
        qos=1
    )


def authenticate(client, payload):
    global session_key

    # MqttAuthChallengeDto:
    #
    # [0] ServerNonce
    # [1] ExpiresAtUnixMs

    challenge = msgpack.unpackb(
        payload,
        raw=False
    )

    server_nonce = challenge[0]
    expires_at = challenge[1]

    if int(time.time() * 1000) > expires_at:
        raise RuntimeError("Authentication challenge expired")

    client_nonce = os.urandom(16)

    secret = API_KEY_SECRET.encode("utf-8")

    # HMAC input:
    #
    # serverNonce || clientNonce || clientId

    proof = hmac.new(
        secret,
        server_nonce
        + client_nonce
        + CLIENT_ID.encode("utf-8"),
        hashlib.sha256
    ).digest()

    # MqttAuthResponseDto:
    #
    # [0] ClientNonce
    # [1] Auth

    response = msgpack.packb(
        [
            client_nonce,
            proof
        ],
        use_bin_type=True
    )

    result = client.publish(
        AUTH_RESPONSE_TOPIC,
        response,
        qos=1
    )

    result.wait_for_publish()

    # Derive the AES-256 session key.

    session_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=server_nonce + client_nonce,
        info=b"SCADA-ENC-v1"
    ).derive(secret)

    # Authentication processing occurs before SCADALGERIA acknowledges
    # the QoS-1 authentication publish. We can now subscribe to commands.

    client.subscribe(
        COMMAND_TOPIC,
        qos=1
    )

    print("Authenticated with SCADALGERIA")
    print("Subscribed to SCADALGERIA commands")


# =====================================================================
# STEP 6 OF 9 — TELEMETRY
#
# This block converts a normal Python number into the SCADALGERIA MQTT
# telemetry format.
#
# The inner MessagePack payload is:
#
#     TagFrameBatchDto
#         ↓
#     AES-256-GCM encryption
#         ↓
#     AesGcmEnvelopeDto
#         ↓
#     MQTT publish
#
# The function send_value() is deliberately simple from the caller's
# perspective:
#
#     send_value(client, 42.5)
# =====================================================================


def next_sequence():
    global last_sequence

    # Use Unix milliseconds as the baseline so a normal process restart
    # does not restart the replay sequence from zero.

    current = int(time.time() * 1000)

    last_sequence = max(
        current,
        last_sequence + 1
    )

    return last_sequence


def next_nonce():
    global nonce_counter

    nonce_counter += 1

    return nonce_counter.to_bytes(
        12,
        byteorder="big"
    )


def encrypt_payload(plaintext):
    sequence = next_sequence()
    timestamp_ms = int(time.time() * 1000)

    nonce = next_nonce()

    # SCADALGERIA AES-GCM AAD:
    #
    # Seq      -> uint64 big-endian
    # TsUnixMs -> uint64 big-endian

    aad = struct.pack(
        ">QQ",
        sequence,
        timestamp_ms
    )

    encrypted = AESGCM(
        session_key
    ).encrypt(
        nonce,
        plaintext,
        aad
    )

    ciphertext = encrypted[:-16]
    gcm_tag = encrypted[-16:]

    # AesGcmEnvelopeDto:
    #
    # [0] KeyId
    # [1] Nonce
    # [2] Ciphertext
    # [3] GcmTag
    # [4] Seq
    # [5] TsUnixMs

    envelope = [
        API_KEY_ID,
        nonce,
        ciphertext,
        gcm_tag,
        sequence,
        timestamp_ms
    ]

    return msgpack.packb(
        envelope,
        use_bin_type=True
    )


def send_value(client, value):

    # TagFrameDto:
    #
    # [0] Address
    # [1] ValNum
    # [2] ValStr
    # [3] TsUnixMs
    # [4] Quality
    #
    # Quality 192 = Good.

    frame = [
        TAG_ADDRESS,
        float(value),
        None,
        0,
        192
    ]

    # TagFrameBatchDto:
    #
    # [0] Frames
    # [1] TraceId
    # [2] SchemaVersion

    batch = [
        [frame],
        None,
        1
    ]

    plaintext = msgpack.packb(
        batch,
        use_bin_type=True
    )

    payload = encrypt_payload(
        plaintext
    )

    client.publish(
        TELEMETRY_TOPIC,
        payload,
        qos=1
    )

    print(f"SCADALGERIA telemetry: {value:.2f}")


# =====================================================================
# STEP 7 OF 9 — RECEIVE AND DECRYPT COMMANDS
#
# SCADALGERIA sends commands to:
#
#     scada/{tenant}/{project}/{source}/commands
#
# Commands use the same AES-256-GCM session encryption.
#
# After decryption, CommandDto is a MessagePack array:
#
#     [0] Id
#     [1] TagAddress
#     [2] Value
#     [3] Timestamp
#     [4] Expiry
#     [5] LeasedUntil
#     [6] TraceId
#     [7] RequiresConfirmation
#     [8] Confirmed
#     [9] SchemaVersion
# =====================================================================


def decrypt_payload(payload):

    envelope = msgpack.unpackb(
        payload,
        raw=False
    )

    key_id = envelope[0]
    nonce = envelope[1]
    ciphertext = envelope[2]
    gcm_tag = envelope[3]
    sequence = envelope[4]
    timestamp_ms = envelope[5]

    if key_id.lower() != API_KEY_ID.lower():
        raise RuntimeError(
            "SCADALGERIA command was encrypted for another API key"
        )

    aad = struct.pack(
        ">QQ",
        sequence,
        timestamp_ms
    )

    return AESGCM(
        session_key
    ).decrypt(
        nonce,
        ciphertext + gcm_tag,
        aad
    )


def receive_command(payload):

    plaintext = decrypt_payload(
        payload
    )

    command = msgpack.unpackb(
        plaintext,
        raw=False,
        timestamp=3
    )

    return command


# =====================================================================
# STEP 8 OF 9 — EXECUTE COMMAND AND SEND RESULT
#
# For this demonstration, SCADALGERIA can control the sine generator
# using three TagAddress values:
#
#     sine.amplitude
#     sine.center
#     sine.period
#
# Examples:
#
#     sine.amplitude = 20
#     sine.center    = 60
#     sine.period    = 120
#
# The external application executes the requested change and publishes
# CommandResultDto back to SCADALGERIA through:
#
#     commands/result
#
# This demonstrates the complete SCADALGERIA command lifecycle without
# controlling any real machinery.
# =====================================================================


def send_command_result(
    client,
    command_id,
    success,
    error=None
):
    # CommandResultDto:
    #
    # [0] CommandId
    # [1] Success
    # [2] ErrorMessage
    # [3] Data
    # [4] SchemaVersion

    result = [
        command_id,
        success,
        error,
        None,
        1
    ]

    plaintext = msgpack.packb(
        result,
        use_bin_type=True
    )

    encrypted = encrypt_payload(
        plaintext
    )

    client.publish(
        COMMAND_RESULT_TOPIC,
        encrypted,
        qos=1
    )


def execute_command(client, command):
    global sine_amplitude
    global sine_center
    global sine_period

    command_id = command[0]
    address = command[1]
    value = command[2]

    expiry = command[4]
    requires_confirmation = command[7]
    confirmed = command[8]

    try:

        # Reject an expired SCADALGERIA command.

        if int(time.time()) > expiry:
            raise ValueError(
                "Command expired"
            )

        # Respect the SCADALGERIA operator-confirmation boundary.

        if requires_confirmation and not confirmed:
            raise ValueError(
                "Command requires confirmation"
            )

        # Minimal demo commands.

        if address == "sine.amplitude":
            sine_amplitude = float(value)

        elif address == "sine.center":
            sine_center = float(value)

        elif address == "sine.period":
            period = float(value)

            if period <= 0:
                raise ValueError(
                    "Period must be greater than zero"
                )

            sine_period = period

        else:
            raise ValueError(
                f"Unsupported SCADALGERIA command: {address}"
            )

        print(
            f"SCADALGERIA command executed: "
            f"{address} = {value}"
        )

        send_command_result(
            client,
            command_id,
            True
        )

    except Exception as error:

        print(
            f"SCADALGERIA command failed: {error}"
        )

        send_command_result(
            client,
            command_id,
            False,
            str(error)
        )


# MQTT messages arrive through this callback.

def on_message(client, userdata, message):

    try:

        if message.topic == AUTH_CHALLENGE_TOPIC:

            authenticate(
                client,
                message.payload
            )

        elif message.topic == COMMAND_TOPIC:

            command = receive_command(
                message.payload
            )

            execute_command(
                client,
                command
            )

    except Exception as error:
        print(
            "SCADALGERIA MQTT message error:",
            error
        )


# =====================================================================
# STEP 9 OF 9 — MAIN LOOP
#
# This is the actual external application.
#
# The sequence is:
#
#     connect
#         ↓
#     authenticate with SCADALGERIA
#         ↓
#     subscribe to SCADALGERIA commands
#         ↓
#     generate sine value
#         ↓
#     send telemetry to SCADALGERIA
#         ↓
#     wait one second
#         ↓
#     repeat forever
#
# MQTT runs in a background thread, so SCADALGERIA commands can arrive
# and execute while the main loop continues generating telemetry.
#
# Using elapsed monotonic time rather than a fixed sample counter keeps
# the wave tied to real elapsed time.
# =====================================================================


client = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    client_id=CLIENT_ID,
    clean_session=True,
    protocol=mqtt.MQTTv311
)

# SCADALGERIA sends only the canonical username during MQTT CONNECT.
# The API key secret is proven later through the HMAC challenge.

client.username_pw_set(
    username=MQTT_USERNAME
)

client.on_connect = on_connect
client.on_message = on_message

client.connect(
    MQTT_HOST,
    MQTT_PORT,
    keepalive=30
)

client.loop_start()


# Wait until SCADALGERIA authentication completes.

while session_key is None:
    time.sleep(0.1)


print("Starting SCADALGERIA sine-wave telemetry")
print(f"Period: {sine_period} seconds")
print(f"Center: {sine_center}")
print(f"Amplitude: {sine_amplitude}")


start_time = time.monotonic()


while True:

    elapsed = (
        time.monotonic()
        - start_time
    )

    # Standard sine function:
    #
    # value =
    #     center
    #     +
    #     amplitude * sin(2π × elapsed / period)

    value = (
        sine_center
        +
        sine_amplitude
        *
        math.sin(
            2
            * math.pi
            * elapsed
            / sine_period
        )
    )

    send_value(
        client,
        value
    )

    time.sleep(
        SEND_EVERY_SECONDS
    )