# SCADALGERIA Python MQTT Example

This example demonstrates the minimum bidirectional MQTT integration between an external Python application and SCADALGERIA.

The application:

- Connects to SCADALGERIA through MQTT.
- Authenticates using the SCADALGERIA challenge-response protocol.
- Generates a continuous sine wave.
- Sends one telemetry value every second.
- Receives commands from SCADALGERIA.
- Executes supported commands.
- Sends command results back to SCADALGERIA.

The example is intentionally small so the complete SCADALGERIA communication flow can be understood from a single Python file.

---

## Python Version

Recommended:

```text
Python 3.11+
```

Check the installed version with:

```bash
python --version
```

---

## Required Libraries

Install the required packages with:

```bash
pip install paho-mqtt msgpack cryptography
```

The example uses the following libraries:

| Library | Purpose |
|---|---|
| `paho-mqtt` | Connects the external application to the SCADALGERIA MQTT endpoint and publishes/subscribes to MQTT topics. |
| `msgpack` | Encodes and decodes SCADALGERIA MQTT message structures. |
| `cryptography` | Provides HKDF-SHA256 and AES-256-GCM for the SCADALGERIA encrypted MQTT session. |
| Python standard library | Provides timing, sine-wave generation, HMAC-SHA256, identifiers, Base64URL encoding, and binary operations. |

---

## Communication Flow

```text
                         SCADALGERIA
                              │
                              │
                    MQTT connection starts
                              │
                              ▼
                    Authentication challenge
                              │
                              ▼
                  ┌───────────────────────┐
                  │ External Python Tool  │
                  └───────────────────────┘
                              │
                              │ HMAC authentication response
                              ▼
                         SCADALGERIA
                              │
                              │
                    Session authenticated
                              │
                              ▼
                  AES session key established
                              │
             ┌────────────────┴────────────────┐
             │                                 │
             │                                 │
             ▼                                 ▼
      TELEMETRY FLOW                     COMMAND FLOW
             │                                 │
             │                                 │
     Generate sine wave                SCADALGERIA sends
             │                         encrypted command
             ▼                                 │
     Create telemetry                          ▼
             │                         Python tool decrypts
             ▼                                 │
     Encrypt telemetry                         ▼
             │                         Execute command
             ▼                                 │
     Publish to SCADALGERIA                    ▼
             │                         Create command result
             │                                 │
             │                                 ▼
             │                         Encrypt command result
             │                                 │
             │                                 ▼
             │                         Publish to SCADALGERIA
             │                                 │
             └────────────────┬────────────────┘
                              │
                              ▼
                         Continue running
                              │
                              ▼
                  Send telemetry every second
```

The MQTT connection remains active while telemetry and command handling operate at the same time.

---

# Example Signal

The Python application generates a sine wave continuously.

Default configuration:

```text
Publish interval:  1 second
Wave period:       300 seconds
Center:            50
Amplitude:         40
```

The generated value is:

```text
value = center + amplitude × sin(2π × elapsed_time / period)
```

With the default configuration:

```text
Minimum value: 10
Maximum value: 90
Full cycle:    5 minutes
Samples/cycle: 300
```

The resulting telemetry approximately follows:

```text
Time         Value

0 sec          50
75 sec         90
150 sec        50
225 sec        10
300 sec        50
375 sec        90
450 sec        50
...            ...
```

The wave continues indefinitely.

---

# SCADALGERIA Configuration

The values obtained from SCADALGERIA are kept together near the top of the Python file.

```python
MQTT_HOST = "your-vps-ip-or-domain"
MQTT_PORT = 1883

TENANT_ID = "..."
PROJECT_ID = "..."

SOURCE_ID = "..."

API_KEY_ID = "..."
API_KEY_SECRET = "..."

TAG_ADDRESS = "sine_wave"
```

These values identify the SCADALGERIA tenant, project, MQTT source, API key, and telemetry address used by the external application.

The `TAG_ADDRESS` must correspond to an address configured for the selected source in SCADALGERIA.

---

# Code Structure

The example is divided into nine sections.

## Step 1 — Imports

Loads the Python modules required for:

- MQTT communication.
- MessagePack serialization.
- Authentication.
- Encryption.
- Timing.
- Sine-wave generation.

No SCADALGERIA connection is performed in this section.

---

## Step 2 — Values From SCADALGERIA

Contains the configuration supplied by SCADALGERIA:

```text
MQTT host
MQTT port
Tenant ID
Project ID
Source ID
API key ID
API key secret
Tag address
```

Keeping these values together makes it clear which configuration belongs to SCADALGERIA and which settings belong only to the example application.

---

## Step 3 — Local Application Settings

Defines the behavior of the sine-wave generator.

For example:

```python
SEND_EVERY_SECONDS = 1.0

sine_period = 300.0
sine_center = 50.0
sine_amplitude = 40.0
```

These values are controlled by the external application.

They are not SCADALGERIA credentials.

---

## Step 4 — MQTT Identity and Topics

Builds the MQTT identity and topic names required by SCADALGERIA.

The external application derives:

```text
MQTT client ID
MQTT username
Authentication challenge topic
Authentication response topic
Telemetry topic
Command topic
Command result topic
```

The main application therefore does not need to manually construct MQTT topic strings every time it sends or receives data.

---

## Step 5 — Authentication

SCADALGERIA uses a challenge-response process instead of sending the API key secret directly as the MQTT password.

The process is:

```text
MQTT CONNECT
     │
     ▼
SCADALGERIA sends challenge
     │
     ▼
Python generates client nonce
     │
     ▼
HMAC-SHA256 proof generated
     │
     ▼
Authentication response published
     │
     ▼
HKDF-SHA256 derives session key
     │
     ▼
Encrypted MQTT session is ready
```

The API key secret proves that the external application is authorized without transmitting that secret as the MQTT password.

---

## Step 6 — Send Telemetry

The sine-wave value is converted into the SCADALGERIA telemetry structure.

The process is:

```text
Python numeric value
        │
        ▼
SCADALGERIA telemetry structure
        │
        ▼
MessagePack
        │
        ▼
AES-256-GCM encryption
        │
        ▼
MQTT telemetry publish
        │
        ▼
SCADALGERIA
```

The main loop only needs to call:

```python
send_value(client, value)
```

The serialization and encryption details remain inside the telemetry functions.

---

## Step 7 — Receive Commands

The external application subscribes to its SCADALGERIA command topic.

When a command arrives:

```text
SCADALGERIA command
        │
        ▼
Encrypted MQTT payload
        │
        ▼
AES-256-GCM decryption
        │
        ▼
MessagePack decoding
        │
        ▼
Command available to Python
```

The command can then be passed to the execution step.

---

## Step 8 — Execute Commands and Send Results

The example supports three demonstration commands:

```text
sine.amplitude
sine.center
sine.period
```

For example:

```text
sine.amplitude = 20
```

changes the generated sine-wave amplitude.

A command follows this flow:

```text
Command received
      │
      ▼
Validate command
      │
      ▼
Execute requested change
      │
      ▼
Create command result
      │
      ▼
Encrypt result
      │
      ▼
Publish result
      │
      ▼
SCADALGERIA
```

The example therefore demonstrates both directions of communication:

```text
Python → SCADALGERIA telemetry

SCADALGERIA → Python command

Python → SCADALGERIA command result
```

---

## Step 9 — Main Loop

The final section starts the external application.

Its responsibility is intentionally simple:

```text
Connect
   │
   ▼
Authenticate
   │
   ▼
Subscribe to commands
   │
   ▼
Generate sine-wave value
   │
   ▼
Send telemetry
   │
   ▼
Wait one second
   │
   └───────────────► Repeat forever
```

MQTT processing runs alongside the telemetry loop, allowing SCADALGERIA commands to be received while the sine wave continues to be generated.

---

# Complete Runtime Flow

The complete application lifecycle can be summarized as:

```text
START
  │
  ▼
Load SCADALGERIA configuration
  │
  ▼
Create MQTT client
  │
  ▼
Connect to SCADALGERIA
  │
  ▼
Authenticate
  │
  ▼
Derive encrypted session key
  │
  ▼
Subscribe to commands
  │
  ▼
┌──────────────────────────────────────────────┐
│                RUN CONTINUOUSLY              │
│                                              │
│  Generate sine wave                         │
│         │                                    │
│         ▼                                    │
│  Send encrypted telemetry ──► SCADALGERIA   │
│                                              │
│  SCADALGERIA command ───────► Receive        │
│                                  │           │
│                                  ▼           │
│                               Execute        │
│                                  │           │
│                                  ▼           │
│  SCADALGERIA ◄──────── Send command result  │
│                                              │
└──────────────────────────────────────────────┘
```

---

# Running the Example

After entering the required SCADALGERIA configuration values, start the application with:

```bash
python scadalgeria_mqtt_example.py
```

A successful session will conceptually progress through:

```text
Connected to SCADALGERIA
Authenticated
Subscribed to commands
Starting sine wave

Telemetry: 50.00
Telemetry: 50.84
Telemetry: 51.67
Telemetry: 52.51
...
```

The application continues sending one telemetry sample every second while remaining available for SCADALGERIA commands.

---

# Purpose of the Example

This example is intended to show the minimum structure required for a bidirectional external SCADALGERIA MQTT integration.

It demonstrates:

```text
SCADALGERIA configuration
        ↓
MQTT connection
        ↓
Authentication
        ↓
Encrypted telemetry
        ↓
Command reception
        ↓
Command execution
        ↓
Encrypted command result
```

A real device, gateway, PLC adapter, simulator, or external application can replace the sine-wave generator with its actual data acquisition and control logic while keeping the same SCADALGERIA communication structure.