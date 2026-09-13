# Application Ownership Restructuring

This first-stage restructuring separates ownership from the Qt input facade.
It is deliberately not an implementation of every finding from the September
2026 architecture review. Driver behavior and profile activation semantics need
separate, explicitly reviewed changes.

## Runtime Structure

```text
qt_qml_app.main
  ControllerConfig
    ProfileRepository: profile files, safe IDs, atomic replacement
  ApplicationServices: Qt lifetime owner
    TelemetryClient
    CloudClient
    UpdateChecker
  ControllerBridge: the QML context object
    ControllerOutput: backend instances and selection policy
      VJoyInterface
      ViGEmInterface
    injected ApplicationServices
    existing shaping, smoothing, and Game Mode integration
```

### Controller Output

`src/controller_output.py` owns backend construction, reuse, and selection.
Constructors are injected, so selection can be tested without Qt or driver
imports. The bridge delegates selection to this owner while retaining its
existing `_vjoy`, `_vigem`, and `_use_vigem` accessors for the Windows probes and
Game Mode implementation.

Full Game Mode's on-demand creation also uses `ControllerOutput.ensure_vigem`.
It honors the injected factory and availability flag without selecting a new
output mode or changing the controller reports.

This owner does not write input, reset devices, or serialize gamepad reports.
Selection intentionally retains the previous package-availability policy,
including its connection-failure limitations. Shaping still happens in the
bridge, and no kernel/client contract or input rate is changed.

### Profile Repository

`src/profile_repository.py` owns profile listing, loading, saving, duplication
IDs, deletion, reset, timestamps, and bundled-default installation. Configuration
retains the existing public CRUD methods and delegates file operations to the
repository. Cloud merge uses the same boundary rather than writing filenames
it constructs itself.

Identifiers must represent a single valid Windows filename stem and resolve
inside the selected directory. Invalid IDs and non-object JSON documents are
rejected. Writes use a temporary file in the destination directory followed by
`os.replace`, so a serialization or replacement failure preserves the old file.
Layout Save As generates a unique safe ID and reports the actual save result.

Resetting the active profile refreshes the bridge and canvas together before
the next save. Deleting the current profile notifies the UI of the fallback.
Cloud pulls emit a per-profile update signal so active-profile state is refreshed
even when a later sync operation fails. Failed uploads make the sync result fail
rather than reporting success for a partial transfer.

The profile JSON schema, unknown fields, per-machine settings location, profile
overlay rules, cloud conflict policy, and bundled-profile retirement policy are
unchanged. This is not yet full widget-schema validation or a migration system.

### Application Services

`src/application_services.py` constructs cloud, telemetry, and updater instances
once, parents them explicitly, and provides idempotent shutdown. The bridge
receives this owner as a constructor argument; it never searches its QObject
parent for services.

The existing account, privacy, and update QML components access Qt properties,
slots, and signals on `controller`. Service implementation objects are no longer
separate context properties in the production entry point. Bridge construction
without services remains supported for controller-only probes.

Privacy commands reach the live telemetry client, not just stored config.
Revocation purges the affected categories from memory and disk, and both upload
paths filter against current consent again. Sentry events are rebuilt from an
allowlist containing exception types, fixed explanatory text, the application
release, and a hashed fingerprint. Exception messages, locals, breadcrumbs,
requests, user information, and arbitrary extras are not forwarded. Collection
remains opt-in; endpoints and credential handling are unchanged.

The updater uses asynchronous `QNetworkAccessManager` requests instead of a
blocking QThread. A 10-second total deadline is not extended by incoming chunks,
the manifest is bounded to 1 MiB, and shutdown aborts the reply without waiting
on network progress. Late completions are ignored. Production startup shuts
services down after bridge initialization failures as well as QML load failures.

Production Main.qml now exposes Account and Privacy entries under Settings,
Check for Updates under Help, and an update notice in the window header so it
does not overlap controller widgets. The dialogs scroll within small windows.
Browser sign-in buttons are disabled with an explanation because callback
delivery is not implemented. Email authentication remains the existing service
implementation; no live account or external service verification is claimed.

## Verification

Run these from the repository root with the project virtual environment:

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_controller_output tests.test_profile_repository tests.test_application_services tests.test_bridge_services tests.test_telemetry_privacy tests.test_updater_requests tests.test_application_startup -v
.\venv\Scripts\python.exe tests\test_stick_shaping.py
```

The new suite uses standard-library `unittest`, temporary profile directories,
fake backends and services, and offscreen Qt with the Basic control style. It
checks backend reuse, unchanged selection/input policy, file containment,
failure recovery, configuration CRUD, cloud persistence, service lifetime,
consent changes, Qt metadata, component bindings, and production QML startup.
The startup integration test constructs real configuration, services, and bridge,
runs a Qt event loop, opens dialogs through production menu actions, clicks a
privacy switch, delivers an update response, and checks shutdown. Only external
effects (storage location, credentials, network, drivers, and splash rendering)
are replaced. Additional tests cover mixed queues after opt-out, private Sentry
fields, reset followed by canvas save, failed uploads, and trickling/stalled
update requests. The regression suite currently contains 43 tests; the shaping
script has 27 independent property checks.
It does not use real credentials, network requests, cursor hooks, or drivers.

Native module stubs are restored individually. Restoring the complete
`sys.modules` mapping would unload newly imported compiled dependencies such as
NumPy and make subsequent startup imports unsafe.

## Follow-Up Work

1. Define and approve neutralization policy for profile/output switches, widget
   destruction, Game Mode exit, and application shutdown. Add backend contract
   tests before changing live input behavior.
2. Give Game Mode and normal input one serialized report writer. Preserve or
   explicitly revise the current pulse values and emergency-stop behavior.
3. Extract Game Mode lifecycle coordination from the bridge once those state
   transitions have hardware-backed regression coverage.
4. Define connection-based fallback and capabilities independently of profile
   layout, then validate both Windows backends.
5. Replace profile overlays and disk/cache mixtures with a validated active
   snapshot. Add widget-schema validation and migrations without losing custom
   fields or user-created layouts.
6. Migrate per-machine settings from the executable working directory to the
   existing user-data directory, with an explicit precedence and rollback rule.
7. Move cloud and telemetry network operations off the UI thread. Authentication,
   sync, and telemetry HTTP calls remain synchronous in this stage.
8. Review cloud timestamp/conflict handling and implement secure browser callback
   delivery. Failed uploads and active-profile refresh are covered, but full
   conflict resolution and OAuth are not implemented by this change.

No driver installation, real input probes, kernel stress tests, WDK checks, or
CodeQL run is claimed by this stage.