# Cephable Virtual Controller: Analysis and Integration Notes

Analysis of the [Cephable/Cephable-VirtualController-Sample](https://github.com/Cephable/Cephable-VirtualController-Sample)
repository and what it means for Nimbus. Cephable is an accessibility input
platform: it captures adaptive input (face expressions, head gestures, voice,
virtual buttons) and dispatches normalized commands to client apps over a
network hub. The sample repo shows how a third-party app registers as a
"virtual controller" and receives those commands.

Date of analysis: 2026-05-27. Sample repo last pushed 2026-03-01, MIT licensed.

## Summary

Cephable solves the half of the problem Nimbus does not: **capturing adaptive
input and shipping normalized commands over a hub**. It explicitly stops short
of injecting into a virtual gamepad or keyboard. Nimbus already owns that output
stage (vJoy / ViGEm backends). The two are complementary. A `DeviceCommand`
handler that drives the existing Nimbus output layer would let Cephable act as
one more input source feeding Nimbus.

The single most useful artifact in the repo is the `sample-data/` folder: a set
of JSON fixtures describing the event schema, replayable offline with no account
or network connection.

## What the sample actually is

It is a thin client SDK pattern, not an input-injection engine. Every
language example does the same three things and then stops:

1. Authenticate a Cephable user via an OAuth authorization-code flow (browser).
2. Register a "user device" and obtain a device token through the REST API.
3. Open a SignalR (WebSocket) connection to the device hub and listen for
   `DeviceCommand` events.

What you do with a received command is left entirely to the integrator. The
samples only print the command to the console.

## Repository layout

Everything lives under `src/`.

| Example            | Language   | Scope                                                              |
| ------------------ | ---------- | ------------------------------------------------------------------ |
| `dotnet/`          | C# / .NET  | Simplest. Paste a token, connect, print commands (~30 lines)       |
| `browser/`, `browser-raw/` | JS / Node | Full OAuth flow plus device/token creation in-browser      |
| `android/`         | Kotlin     | Full sign-in plus device registration, Android Studio project      |
| `iOS/`             | Swift      | SwiftUI equivalent of the Android sample                           |
| `c++/e2e/`         | C++        | End-to-end: localhost OAuth listener, device registration, SignalR |
| `c++/simple/`, `c++/winhttp*` | C++ | Dependency variants (WinHTTP, libHttpClient)               |
| `sample-data/`     | JSON       | Event-schema fixtures (the valuable part)                          |

The C++ examples vendor large dependencies (`cpprestsdk`, `SignalR-Client-Cpp`
as git submodules, plus a committed `vcpkg_installed/` tree), so a clone is
heavier than the application code suggests.

## The protocol, distilled

The C# sample is the cleanest reference for the hub connection:

```csharp
var connection = new HubConnectionBuilder()
    .WithUrl("https://services.cephable.com/device", options => {
        options.AccessTokenProvider = () => Task.FromResult(deviceToken);
        options.Headers.Add("X-Device-Token", deviceToken);
    }).Build();

connection.On<string>("DeviceCommand", command => { /* handle command */ });
await connection.StartAsync();
await connection.InvokeAsync("VerifySelf");   // signal the hub you are listening
```

The C++ end-to-end client fills in the auth and registration steps:

- **Browser auth.** Open
  `https://services.cephable.com/signin?client_id=...&redirect_uri=http://localhost:8080/callback`,
  capture the `code` query parameter on a localhost listener, then
  `POST /signin/token` (grant_type=code) to exchange it for an `access_token`.
- **Device registration.** `POST /api/Device/userDevices/new/{deviceTypeId}`
  to create the device, then `POST /api/Device/userDevices/{id}/tokens` to get
  the `deviceToken` used for the hub connection.
- **Hub.** Connect to `{apiUrl}/device`, subscribe to `DeviceCommand`, invoke
  `VerifySelf`.

Other subscribable hub events documented in the README:
`DeviceProfileUpdate`, `DeviceSettingsUpdate`, `DeviceType`, `StartListening`,
`StopListening`.

## The event schema (sample-data)

These fixtures are vendored into this repo at
[tests/fixtures/cephable/](../../tests/fixtures/cephable/) (the seven MIT-licensed
files from Cephable's `src/sample-data/`, with their original `README.md` kept
alongside for provenance). They can be replayed without a Cephable account or
network connection.

This is the real specification, and it is replayable offline. A macro is
`{ description, steps[] }`. Each step carries `commands[]` (the spoken or
triggered phrase) and `events[]`. Event shape:

```json
{
  "eventType": "KeyPress",
  "keys": ["space"],
  "holdTimeMilliseconds": 0,
  "isKeyLatch": false,
  "typedPhrase": null,
  "mouseMoveX": null, "mouseMoveY": null, "mouseMoveScroll": null,
  "joystickLeftMoveX": null, "joystickLeftMoveY": null,
  "joystickRightMoveX": null, "joystickRightMoveY": null,
  "outputSpeech": null, "audioFileUrl": null,
  "deviceTypeCustomActionId": null, "deviceTypeId": null,
  "additionalInputContent": null
}
```

Event types observed across the fixtures:

- `KeyPress` / `KeyRelease` / `KeyToggle` (latch).
- `JoystickMove` with axis values normalized to a -100..100 range
  (`joystickLeftMoveX/Y`, `joystickRightMoveX/Y`).
- `Pause` with `holdTimeMilliseconds` for sequencing multi-step macros.

Key/button namespace (`key_and_button_values.json`): keyboard keys, modifiers,
media keys, `mouse_button_1..3`, and `gamepad_button_1..16`.

The README explicitly notes that clients must track held keys and auto-release
them after a timeout, which is a stateful concern (latch model plus
`holdTimeMilliseconds`).

## Relevance to Nimbus

- **Complementary, not competing.** Cephable captures adaptive input and
  normalizes it to commands. Nimbus injects into virtual devices. The seam
  between them is a `DeviceCommand` handler.
- **Direct mapping to the output layer.** The -100..100 joystick range and
  `gamepad_button_N` naming map almost directly onto a ViGEm Xbox pad. Keyboard
  values map onto the keyboard-output path.
- **Ready-made test corpus.** The `sample-data` JSON files (now vendored at
  [tests/fixtures/cephable/](../../tests/fixtures/cephable/)) can be replayed
  against the Nimbus input pipeline as fixtures, with no account or network, to
  validate the full event-to-output path including latching and timed release.

## A possible integration path

1. **Replay first.** Add a small loader that reads the vendored fixtures at
   [tests/fixtures/cephable/](../../tests/fixtures/cephable/) and feeds events
   through the existing Nimbus input pipeline. This validates the mapping with
   zero external dependencies and doubles as a regression test.
2. **Event mapper.** Implement a translation layer:
   - `KeyPress` / `KeyRelease` / `KeyToggle` -> keyboard / gamepad button output,
     honoring `isKeyLatch` and `holdTimeMilliseconds`.
   - `JoystickMove` -> scale -100..100 to the backend axis range.
   - `Pause` -> sequence delay within a macro.
3. **Live source (optional, gated on account access).** Wrap the SignalR hub
   client as an optional Nimbus input source. On `DeviceCommand`, route through
   the same mapper used for replay.

This keeps the network/account dependency at the edge and reuses one mapper for
both offline fixtures and live input.

## Caveats

- **Account and licensing required for live use.** Running the networked
  samples needs a Cephable account plus a licensed API client ID and device
  type ID obtained from Cephable (support@cephable.com). The offline fixtures
  do not.
- **Sample auth is not production-grade.** Tokens are pasted into a console,
  the C++ launcher uses `std::system("open ...")` (macOS-only), the callback is
  a fixed `localhost:8080`, and there is no token refresh.
- **`DeviceCommand` payload is a string.** Both the C# and C++ handlers receive
  it as a plain string. Confirm whether it is a raw command name or serialized
  JSON before relying on the richer event schema over the wire (the
  `sample-data` schema is documented but the hub example only prints a string).

## References

- Sample repo: https://github.com/Cephable/Cephable-VirtualController-Sample
- API / Swagger: https://services.cephable.com/swagger
- Developer portal: https://portal.cephable.com
- Cephable app download: https://cephable.com/download
- Vendored fixtures: [tests/fixtures/cephable/](../../tests/fixtures/cephable/)
- Related Nimbus docs: [AAC_INTEGRATION.md](AAC_INTEGRATION.md),
  [HARDWARE_INTEGRATION.md](HARDWARE_INTEGRATION.md)
