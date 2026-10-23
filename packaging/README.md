Build on Windows x64 with PowerShell:

```powershell
./packaging/prepare-compiler.ps1
./packaging/build-windows.ps1
```

The build downloads uv 0.12.22 from Astral's official immutable release and checks its pinned SHA-256. uv prepares CPython 3.12.15 from its pinned managed-Python metadata without registering Python or changing PATH. The compiler preparation checks the official Inno Setup 6.7.3 installer Authenticode signature and publisher before execution. `-PrepareOnly` produces an unpacked payload for testing without the compiler.

Only explicitly named application modules, static files, launchers and README enter the payload. `build-manifest.json` records every included file's checksum. Optional GPU libraries are installed later, after the user requests environment setup. No datasets, exports, credentials or local app database are bundled.

The per-user installer targets `%LOCALAPPDATA%\Programs\MultiGPU` and creates a Start menu entry. The tray launcher creates its isolated environment at `%LOCALAPPDATA%\MultiGPU\environment`. The default workspace is `%LOCALAPPDATA%\MultiGPU\workspace`; choose another workspace in the tray menu for the next start. Upgrade and uninstall retain the environment, workspace, exports, and provider-native credentials.

Closing the browser leaves the tray controller running. Tray **Pause training and exit** requests cooperative pauses where supported, warns about remote executions, and shuts down through the authenticated local API. Provider cancellation and billing enforcement still depend on the provider. The installer is unsigned until a release publisher signs it; Windows may display a reputation warning.

Run `./packaging/test-payload.ps1` after building to exercise the bundled runtime and an isolated workspace without a system Python installation. This verifies an unpacked payload on the current Windows host; a separate clean Windows 10/11 VM remains the release gate for installer, SmartScreen, CUDA and provider-authentication checks.
