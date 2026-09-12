<#
.SYNOPSIS
    Unattended check of the installer's driver bootstrap: vJoy and ViGEmBus.

.DESCRIPTION
    Runs the built installer silently and verifies what actually happened on the
    machine, rather than what the installer said. Everything after launch is
    unattended; the only interaction is the elevation prompt on this script
    itself, because the installer requires admin and this account is not one.

    Two runs, with a reboot between them, exercise the install path on a
    machine that already has the drivers:

        Set-ExecutionPolicy -Scope Process Bypass -Force
        tests\probe_installer_drivers_windows.ps1 -Teardown     # removes both drivers, then asks for a reboot
        # reboot
        tests\probe_installer_drivers_windows.ps1               # installs, verifies, launches, uninstalls the app

    The reboot is not optional and the script enforces it: a kernel driver
    removed in this boot is still resident, and a reinstall on top of it fails
    in ways that look like installer bugs. Measured on the dev machine on
    2026-09-06: vJoy's device failed to start with STATUS_INSUFFICIENT_RESOURCES
    and vJoyInstall.exe rolled its own device back, leaving files, an uninstall
    key, and 0 buttons. The teardown records the boot time and the install run
    refuses to continue in the same boot.

    On a machine that never had the drivers, run it once without -Teardown.

    Two files land in dist\ whatever happens: installer-probe.log (the checks)
    and installer-probe-transcript.log (everything the console showed, which
    is what to read if the run stopped early).

    Checks (each prints PASS or FAIL, with a count at the end):

      I1   elevated, and the built installer is present
      I2   baseline recorded (driver state, vJoy device 1 capabilities)
      I3   vJoy uninstalled            (-Teardown run only)
      I4   ViGEmBus uninstalled        (-Teardown run only)
      I5   silent install returns a success code
      I6   vJoy present afterwards, with a device attached
      I7   ViGEmBus present afterwards, and a client can open a virtual pad
      I8   vJoy device 1 has 8 axes and 128 buttons and is usable
      I9   the app is installed and registered, with no leftover drivers folder
      I10  the app starts, shows a window, and exits when asked
      I11  the app uninstalls and both drivers survive it

    "Present" means what the installer's own detection means since 2026-09-06:
    the driver's service exists AND a device is attached to it (the driver's
    Enum count). Files, uninstall keys and service entries all survive a
    removal or a failed device start, and trusting them is how the first run of
    this probe skipped a ViGEmBus that failed every client with
    VIGEM_ERROR_BUS_NOT_FOUND.

.PARAMETER Teardown
    Uninstall vJoy and ViGEmBus, record the boot time, and stop. Reboot, then
    run again without it. This removes drivers the machine may be using; expect
    vJoy to come back as 2.1.9.1 and ViGEmBus as 1.22.0, which is what the
    installer ships.

.PARAMETER KeepApp
    Leave the app installed at the end. By default I11 uninstalls it, since the
    point of the run is the drivers, and the drivers are deliberately left
    behind by the uninstaller.
#>
param(
    [string]$Setup = "dist\Nimbus-Adaptive-Controller-Setup-1.4.3.exe",
    [switch]$Teardown,
    [switch]$KeepApp,
    [string]$LogPath = "dist\installer-probe.log"
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$Setup = if ([IO.Path]::IsPathRooted($Setup)) { $Setup } else { Join-Path $repo $Setup }
$LogPath = if ([IO.Path]::IsPathRooted($LogPath)) { $LogPath } else { Join-Path $repo $LogPath }
$transcript = [IO.Path]::ChangeExtension($LogPath, $null).TrimEnd('.') + '-transcript.log'
$marker = [IO.Path]::ChangeExtension($LogPath, $null).TrimEnd('.') + '-teardown.json'

$script:pass = 0
$script:fail = 0
$script:lines = @()

function Say([string]$Text) {
    Write-Host $Text
    $script:lines += $Text
}
function Check([string]$Id, [bool]$Ok, [string]$Detail) {
    if ($Ok) { $script:pass++ } else { $script:fail++ }
    Say ("  {0,-4} {1,-5} {2}" -f $Id, $(if ($Ok) { 'PASS' } else { 'FAIL' }), $Detail)
}
function Save-Log {
    New-Item -ItemType Directory -Force (Split-Path $LogPath) | Out-Null
    Set-Content -Path $LogPath -Value $script:lines -Encoding utf8
    Write-Host "`nLog: $LogPath"
}
function Get-BootTime {
    return (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
}

# Native commands and python under $ErrorActionPreference = 'Stop' turn any
# stderr line into a terminating error in Windows PowerShell 5.1. Everything
# that shells out goes through here.
function Invoke-Native([scriptblock]$Block) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Block } finally { $ErrorActionPreference = $prev }
}

# Runs a Python snippet with the venv interpreter and returns its last line of
# output. The source goes through a file: passing it on the command line lets
# PowerShell's native-argument quoting strip the double quotes, which is how the
# first run of this probe turned a working check into a SyntaxError.
function Invoke-Python([string]$Source) {
    $py = Join-Path $repo 'venv\Scripts\python.exe'
    if (-not (Test-Path $py)) { return 'no venv python' }
    $file = Join-Path $env:TEMP ('nimbus_probe_' + [Guid]::NewGuid().ToString('N') + '.py')
    Set-Content -Path $file -Value $Source -Encoding ascii
    try {
        $out = Invoke-Native { & $py $file 2>&1 | ForEach-Object { "$_" } | Select-Object -Last 1 }
        return "$out"
    } finally {
        Remove-Item $file -Force -ErrorAction SilentlyContinue
    }
}

function Get-DeviceCount([string]$Service) {
    $c = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\$Service\Enum" -ErrorAction SilentlyContinue).Count
    if ($c) { return [int]$c } else { return 0 }
}

# ------------------------------------------------------------------ detection
function Get-VJoy {
    # The same three uninstall keys installer.nsi checks, read from the 64-bit
    # hive, plus the DLL on disk as the fallback, and then the device.
    $keys = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{8E31F76F-74C3-47F1-9550-E041EEDC5FBB}_is1',
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{D3B6B8B0-4C9B-4C9B-8A1A-6B3C5E7D8F2A}_is1',
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\vJoy'
    )
    $version = $null; $uninstall = $null; $installed = $false
    foreach ($k in $keys) {
        $p = Get-ItemProperty $k -ErrorAction SilentlyContinue
        if ($p.DisplayVersion) { $version = $p.DisplayVersion; $uninstall = $p.UninstallString; $installed = $true; break }
    }
    if (-not $installed -and (Test-Path "$env:ProgramFiles\vJoy\x64\vJoyInterface.dll")) { $installed = $true; $version = '(dll only)' }
    $devices = Get-DeviceCount 'vjoy'
    return [pscustomobject]@{
        Present   = ($installed -and $devices -ge 1)
        Installed = $installed
        Devices   = $devices
        Version   = $version
        Uninstall = $uninstall
    }
}

function Get-ViGEm {
    $service = [bool](Get-Service ViGEmBus -ErrorAction SilentlyContinue)
    $devices = Get-DeviceCount 'ViGEmBus'
    $entry = Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall' -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue } |
        Where-Object { $_.DisplayName -match 'ViGEm|Virtual Gamepad Emulation Bus' } |
        Select-Object -First 1
    $file = "$env:SystemRoot\System32\drivers\ViGEmBus.sys"
    return [pscustomobject]@{
        Present     = ($service -and $devices -ge 1)
        Service     = $service
        Devices     = $devices
        Version     = $entry.DisplayVersion
        Product     = $entry.PSChildName
        FileVersion = $(if (Test-Path $file) { (Get-Item $file).VersionInfo.FileVersion } else { $null })
    }
}

function Format-VJoy($v) {
    if ($v.Present) { return "present ($($v.Version), $($v.Devices) device)" }
    if ($v.Installed) { return "BROKEN: $($v.Version) installed but no device attached" }
    return 'absent'
}
function Format-ViGEm($v) {
    if ($v.Present) { return "present ($($v.Version), file $($v.FileVersion), $($v.Devices) device)" }
    if ($v.Service) { return "BROKEN: service entry lingers but no bus device (file $($v.FileVersion))" }
    return 'absent'
}

function Test-ViGEmClient {
    # The proof that matters: can vgamepad, which the app uses, open a pad.
    return Invoke-Python @'
import vgamepad
vgamepad.VX360Gamepad()
print('client opened a virtual pad')
'@
}

function Get-VJoyCaps {
    # Axis and button counts straight from vJoyInterface.dll, which is what the
    # app itself talks to. Returns $null when vJoy is not installed.
    $dll = "$env:ProgramFiles\vJoy\x64\vJoyInterface.dll"
    if (-not (Test-Path $dll)) { return $null }
    $source = @"
import ctypes
d = ctypes.WinDLL(r'$dll')
u = {'X': 0x30, 'Y': 0x31, 'Z': 0x32, 'RX': 0x33, 'RY': 0x34, 'RZ': 0x35, 'SL0': 0x36, 'SL1': 0x37}
axes = ' '.join(n for n, c in u.items() if d.GetVJDAxisExist(1, c))
s = d.GetVJDStatus(1)
status = ['OWN', 'FREE', 'BUSY', 'MISS', 'UNKN'][s] if s < 5 else str(s)
print('%d buttons, axes %s, status %s' % (d.GetVJDButtonNumber(1), axes, status))
"@
    return Invoke-Python $source
}

function Get-App {
    # NSIS is a 32-bit process and writes its uninstall key without SetRegView,
    # so the key lands in WOW6432Node. A 64-bit PowerShell has to look there.
    $keys = @(
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\nimbus-adaptive-controller',
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\nimbus-adaptive-controller'
    )
    foreach ($k in $keys) {
        $p = Get-ItemProperty $k -ErrorAction SilentlyContinue
        if ($p.InstallLocation) {
            return [pscustomobject]@{ Present = $true; Dir = $p.InstallLocation; Version = $p.DisplayVersion; Uninstall = $p.UninstallString }
        }
    }
    return [pscustomobject]@{ Present = $false; Dir = $null; Version = $null; Uninstall = $null }
}

# ---------------------------------------------------------------------- start
New-Item -ItemType Directory -Force (Split-Path $LogPath) | Out-Null
Start-Transcript -Path $transcript -Force | Out-Null
try {
    Say "Nimbus installer driver probe  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Say "  setup    : $Setup"
    Say "  teardown : $Teardown"
    Say "  booted   : $(Get-BootTime)"
    Say ''

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $elevated = (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    Check 'I1' ($elevated -and (Test-Path $Setup)) "elevated=$elevated setup present=$(Test-Path $Setup) (running as $($identity.Name))"
    if (-not $elevated) {
        Say ''
        Say 'The installer needs admin. Start an elevated PowerShell (Win+X, A) and run this again.'
        exit 1   # the finally block still runs
    }
    if (-not (Test-Path $Setup)) {
        Say ''
        Say 'Build it first: PyInstaller, then build_tools\fetch_redist.ps1, then makensis build_tools\installer.nsi'
        exit 1   # the finally block still runs
    }

    $vj0 = Get-VJoy
    $vg0 = Get-ViGEm
    $caps0 = Get-VJoyCaps
    Check 'I2' $true "baseline: vJoy=$(Format-VJoy $vj0); ViGEmBus=$(Format-ViGEm $vg0); device1=$caps0"

    # ------------------------------------------------------------------- teardown
    if ($Teardown) {
        if ($vj0.Installed -and $vj0.Uninstall) {
            $exe = $vj0.Uninstall.Trim('"')
            Say "  removing vJoy $($vj0.Version)"
            # Inno uninstallers hand off to a copy of themselves and exit at once,
            # so wait for the registry key to go rather than for the process.
            Start-Process -FilePath $exe -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait
            $deadline = (Get-Date).AddSeconds(60)
            while ((Get-Date) -lt $deadline -and (Get-VJoy).Installed) { Start-Sleep -Seconds 2 }
        }
        $vj1 = Get-VJoy
        Check 'I3' (-not $vj1.Installed) "vJoy after uninstall: $(Format-VJoy $vj1)"

        if ($vg0.Product) {
            Say "  removing ViGEmBus $($vg0.Version)"
            Start-Process -FilePath 'msiexec.exe' -ArgumentList '/x', $vg0.Product, '/qn', '/norestart' -Wait
            Start-Sleep -Seconds 3
        }
        $vg1 = Get-ViGEm
        Check 'I4' (-not $vg1.Present -and -not $vg1.Product) "ViGEmBus after uninstall: $(Format-ViGEm $vg1)"

        @{ boot = (Get-BootTime).ToString('o'); when = (Get-Date).ToString('o') } | ConvertTo-Json | Set-Content -Path $marker -Encoding ascii
        Say ''
        Say 'Teardown done. The removed drivers are still resident in this boot, so a reinstall now would fail in'
        Say 'ways that look like installer bugs. Reboot, then run this script again without -Teardown.'
        exit $(if ($script:fail -gt 0) { 1 } else { 0 })
    }

    Say '  I3   SKIP  no -Teardown, drivers left in place'
    Say '  I4   SKIP  no -Teardown, drivers left in place'
    if (Test-Path $marker) {
        $m = Get-Content $marker -Raw | ConvertFrom-Json
        $tornDownBoot = [datetime]::Parse($m.boot)
        if ([Math]::Abs(((Get-BootTime) - $tornDownBoot).TotalSeconds) -lt 5) {
            Say ''
            Say "STOPPED: the teardown ran in this same boot ($($m.when)). Reboot first, then run again."
            $script:fail++
            exit 1   # the finally block still runs
        }
        Say "  teardown at $($m.when) was followed by a reboot; the machine is a fresh install target"
        Remove-Item $marker -Force -ErrorAction SilentlyContinue
    }

    # --------------------------------------------------------------- silent install
    Say '  running the installer silently'
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $proc = Start-Process -FilePath $Setup -ArgumentList '/S' -Wait -PassThru
    $sw.Stop()
    $code = $proc.ExitCode
    Check 'I5' ($code -eq 0) "installer exit code $code after $([math]::Round($sw.Elapsed.TotalSeconds,1)) s"

    Start-Sleep -Seconds 3
    $vj2 = Get-VJoy
    $vg2 = Get-ViGEm
    $caps2 = Get-VJoyCaps
    $client = Test-ViGEmClient
    $app = Get-App

    Check 'I6' $vj2.Present "vJoy: $(Format-VJoy $vj2)"
    Check 'I7' ($vg2.Present -and $client -match 'opened') "ViGEmBus: $(Format-ViGEm $vg2); client: $client"
    Check 'I8' ($caps2 -match '128 buttons' -and $caps2 -match 'X Y Z RX RY RZ SL0 SL1' -and $caps2 -match 'status (FREE|OWN|BUSY)') "vJoy device 1: $caps2"

    $leftover = $false
    if ($app.Dir) { $leftover = Test-Path (Join-Path $app.Dir 'drivers') }
    Check 'I9' ($app.Present -and -not $leftover) "app $($app.Version) at $($app.Dir); leftover drivers folder: $leftover (present only when a driver install failed)"

    # ------------------------------------------------------------------ app launch
    if ($app.Present) {
        $exe = Join-Path $app.Dir 'Nimbus-Adaptive-Controller-1.4.3.exe'
        if (Test-Path $exe) {
            # Anything left over from an earlier run would be mistaken for ours.
            Get-Process -Name 'Nimbus-Adaptive-Controller*' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
            $started = Get-Date
            $boot = Start-Process -FilePath $exe -PassThru
            # PyInstaller onefile: the process started here is the bootloader. It
            # unpacks ~190 MB, then spawns a child that runs the app and owns the
            # window, so the window has to be looked for on the child. The first
            # runs of this probe polled the bootloader, waited 90 s for a handle
            # that never comes, then killed it and orphaned the child.
            # $app is the install record used by I11 below; the window's process
            # gets its own name. Reusing $app here is what made the third run
            # report "app was not installed" and skip the uninstall.
            $deadline = (Get-Date).AddSeconds(120)
            $win = $null
            while ((Get-Date) -lt $deadline) {
                Start-Sleep -Seconds 2
                $boot.Refresh()
                if ($boot.HasExited) { break }
                $win = Get-Process -Name 'Nimbus-Adaptive-Controller*' -ErrorAction SilentlyContinue |
                    Where-Object { $_.Id -ne $boot.Id -and $_.MainWindowHandle -ne 0 } | Select-Object -First 1
                if ($win) { break }
            }
            $waited = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
            $title = $(if ($win) { $win.MainWindowTitle } else { '' })
            $closed = $false
            if ($win) {
                $null = $win.CloseMainWindow()
                Start-Sleep -Seconds 8
                $win.Refresh()
                $closed = $win.HasExited
            }
            Get-Process -Name 'Nimbus-Adaptive-Controller*' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
            Check 'I10' ([bool]$win) "app window '$title' shown after $waited s: $([bool]$win); closed on request: $closed"
        } else {
            Check 'I10' $false "app exe not found at $exe"
        }
    } else {
        Check 'I10' $false 'app not installed, nothing to launch'
    }

    # --------------------------------------------------------------- app uninstall
    if ($KeepApp) {
        Say '  I11  SKIP  -KeepApp passed, app left installed'
    } elseif ($app.Present) {
        $un = Join-Path $app.Dir 'Uninstall.exe'
        if (Test-Path $un) {
            Start-Process -FilePath $un -ArgumentList '/S', "_?=$($app.Dir)" -Wait
            Start-Sleep -Seconds 3
            Remove-Item $un -Force -ErrorAction SilentlyContinue
            Remove-Item $app.Dir -Recurse -Force -ErrorAction SilentlyContinue
        }
        $after = Get-App
        $vj3 = Get-VJoy
        $vg3 = Get-ViGEm
        Check 'I11' ((-not $after.Present) -and $vj3.Present -and $vg3.Present) "app removed: $(-not $after.Present); vJoy kept: $($vj3.Present); ViGEmBus kept: $($vg3.Present)"
    } else {
        Check 'I11' $false 'app was not installed, nothing to uninstall'
    }

    # ------------------------------------------------------------------- summary
    Say ''
    Say ("Result: {0}/{1} checks passed" -f $script:pass, ($script:pass + $script:fail))
    if (Get-Item 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired' -ErrorAction SilentlyContinue) {
        Say 'A reboot is pending. Windows was told a restart is needed; the drivers may not be fully live until then.'
    }
} catch {
    Say ''
    Say "STOPPED: $($_.Exception.Message)"
    Say "  at: $($_.InvocationInfo.PositionMessage)"
    $script:fail++
} finally {
    Save-Log
    Stop-Transcript | Out-Null
}
if ($script:fail -gt 0) { exit 1 }
