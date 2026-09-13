<#
.SYNOPSIS
    Self-heal: detach the Nimbus Mouse Filter if it is registered but cannot
    load, so a machine that drifted never boots without a mouse.

.DESCRIPTION
    install-dev.ps1 verifies the filter at install time and rolls back if the
    driver does not load. That protects the install. It cannot protect the
    machine afterwards, and the dangerous state is not the install, it is the
    drift: the filter stays registered while the machine stops being willing
    to load it. A class upper filter is mandatory once listed, so the next
    mouse restart or reboot leaves no working mouse at all.

    Causes seen or expected: a Windows update that resets boot configuration,
    "bcdedit /set testsigning off" to play an anti-cheat game, Secure Boot
    switched back on in firmware, or a rebuild signed with a certificate that
    is no longer in the machine stores.

    install-dev.ps1 copies this script and pnp-common.ps1 to
    %ProgramData%\ProjectNimbus\driver and registers it as the scheduled task
    "NimbusMouseFilterGuard", running as SYSTEM at startup and once a day.
    The daily run is the one that matters most: it detaches the filter while
    the mouse is still working, before a reboot would make it fatal.

    It is deliberately timid. It detaches only when it can show the filter is
    registered AND will not load, and it never writes an UpperFilters list
    without mouclass. Everything it does goes to
    %ProgramData%\ProjectNimbus\logs\mouse-guard.log and to the Application
    event log (source "Nimbus Mouse Filter Guard").

    uninstall-dev.ps1 removes the task and these copies.

.PARAMETER TimeoutSeconds
    How long to wait for mouse devices to enumerate at boot before giving up
    and leaving the machine alone.

.PARAMETER DryRun
    Decide and log, but do not change anything.
#>
[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 180,
    [switch]$DryRun
)

$ErrorActionPreference = 'Continue'
$service = 'nimbus_moufilter'
$classKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4D36E96F-E325-11CE-BFC1-08002BE10318}'
$sysPath = Join-Path $env:SystemRoot 'System32\drivers\nimbus_moufilter.sys'
$logDir = Join-Path $env:ProgramData 'ProjectNimbus\logs'
$logPath = Join-Path $logDir 'mouse-guard.log'
$eventSource = 'Nimbus Mouse Filter Guard'

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
if ((Test-Path $logPath) -and (Get-Item $logPath).Length -gt 1MB) {
    Move-Item $logPath "$logPath.old" -Force -ErrorAction SilentlyContinue
}

function Write-Log {
    param([string]$Message, [ValidateSet('Information', 'Warning', 'Error')][string]$Level = 'Information')
    $line = "{0} [{1}] {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level.ToUpper(), $Message
    Write-Host $line
    try { Add-Content -Path $logPath -Value $line -Encoding utf8 -ErrorAction Stop } catch { }
    # Best effort: the source is registered by install-dev.ps1, which is the
    # only thing here that runs elevated enough to create it.
    try { Write-EventLog -LogName Application -Source $eventSource -EntryType $Level -EventId 1 -Message $Message -ErrorAction Stop } catch { }
}

function Get-UpperFilters {
    $v = (Get-ItemProperty -Path $classKey -Name UpperFilters -ErrorAction SilentlyContinue).UpperFilters
    if ($v) { @($v) } else { @() }
}

function Test-WouldLoad {
    <#
        Would Code Integrity accept the .sys in THIS boot? Mirrors
        install-dev.ps1's Test-TestSigningActive; see the notes there.
          0x1 CODEINTEGRITY_OPTION_ENABLED    set on every normal boot
          0x2 CODEINTEGRITY_OPTION_TESTSIGN   test signing
    #>
    $msSigned = $false
    $sigStatus = 'missing'
    if (Test-Path $sysPath) {
        try {
            $sig = Get-AuthenticodeSignature $sysPath
            $sigStatus = [string]$sig.Status
            $msSigned = [bool]($sig.SignerCertificate -and $sig.SignerCertificate.Subject -like '*Microsoft Windows*')
        } catch { $sigStatus = "unreadable ($($_.Exception.Message))" }
    }
    $testSign = $false
    $ciOff = $false
    $known = $false
    try {
        if (-not ('Nimbus.GuardCodeIntegrity' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace Nimbus {
    public static class GuardCodeIntegrity {
        [StructLayout(LayoutKind.Sequential)]
        public struct Info { public uint Length; public uint Options; }
        [DllImport("ntdll.dll")]
        static extern int NtQuerySystemInformation(int infoClass, ref Info info, int length, out int returnLength);
        public static uint Options() {
            Info i = new Info(); i.Length = 8; int rl;
            int st = NtQuerySystemInformation(103, ref i, 8, out rl);
            if (st != 0) throw new InvalidOperationException("NtQuerySystemInformation failed 0x" + st.ToString("X8"));
            return i.Options;
        }
    }
}
'@
        }
        $options = [Nimbus.GuardCodeIntegrity]::Options()
        $testSign = (($options -band 0x2) -ne 0)
        $ciOff = (($options -band 0x1) -eq 0)
        $known = $true
    } catch { }

    # Unknown Code Integrity state must not trigger a detach: refusing to act
    # on a machine we cannot read is safer than stripping a working filter.
    $wouldLoad = if (-not $known) { $true } else { $msSigned -or $testSign -or $ciOff }
    if ($known -and $wouldLoad -and -not $msSigned -and $sigStatus -ne 'Valid' -and -not $ciOff) { $wouldLoad = $false }

    [pscustomobject]@{
        WouldLoad = $wouldLoad
        Known     = $known
        TestSign  = $testSign
        CiOff     = $ciOff
        MsSigned  = $msSigned
        SigStatus = $sigStatus
    }
}

function Invoke-Detach([string]$reason) {
    Write-Log "DETACHING the filter: $reason" 'Warning'
    if ($DryRun) { Write-Log 'DryRun: nothing was changed.'; return }

    $current = Get-UpperFilters
    $remaining = @($current | Where-Object { $_ -ne $service })
    # mouclass is the mouse class driver itself. A list without it, or a
    # deleted value, stops every mouse. Never write one.
    if ($remaining -notcontains 'mouclass') { $remaining = @('mouclass') + $remaining }
    Set-ItemProperty -Path $classKey -Name UpperFilters -Value ([string[]]$remaining) -Type MultiString
    Write-Log "UpperFilters is now: $($remaining -join ', ')"

    . (Join-Path $PSScriptRoot 'pnp-common.ps1')   # Restart-Mice
    Restart-Mice

    $mice = @(Get-PnpDevice -Class Mouse -PresentOnly -ErrorAction SilentlyContinue)
    $bad = @($mice | Where-Object { $_.Status -ne 'OK' })
    if ($mice.Count -gt 0 -and $bad.Count -eq 0) {
        Write-Log "Recovered: $($mice.Count) mouse device(s) reporting OK. Re-arm with driver\install-dev.ps1 once signing is sorted out."
    } else {
        Write-Log "After detaching, $($bad.Count) of $($mice.Count) mouse device(s) still report a problem. A reboot should clear it; the UpperFilters entry is gone." 'Error'
    }
}

Write-Log "Guard starting (timeout ${TimeoutSeconds}s, dry run: $([bool]$DryRun))"

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ($true) {
    if (-not ((Get-UpperFilters) -contains $service)) {
        Write-Log 'Filter is not registered as a mouse class filter. Nothing to do.'
        break
    }

    $drv = Get-CimInstance Win32_SystemDriver -Filter "Name='$service'" -ErrorAction SilentlyContinue
    if ($drv -and $drv.State -eq 'Running') {
        Write-Log 'Filter is registered and running. Healthy.'
        break
    }

    $load = Test-WouldLoad
    if (-not $load.WouldLoad) {
        Invoke-Detach ("registered but it will not load in this boot (signature: $($load.SigStatus), test signing: $($load.TestSign), Microsoft-signed: $($load.MsSigned))")
        break
    }

    $mice = @(Get-PnpDevice -Class Mouse -PresentOnly -ErrorAction SilentlyContinue)
    if ($mice.Count -gt 0) {
        $bad = @($mice | Where-Object { $_.Status -ne 'OK' })
        if ($bad.Count -eq 0) {
            Write-Log "Filter is registered and loadable; $($mice.Count) mouse device(s) OK. Healthy."
            break
        }
        if ($bad.Count -eq $mice.Count) {
            $codes = foreach ($m in $bad) {
                try { ($m | Get-PnpDeviceProperty -KeyName 'DEVPKEY_Device_ProblemCode' -ErrorAction Stop).Data } catch { '?' }
            }
            Invoke-Detach "registered, not running, and no mouse is starting (problem codes: $($codes -join ', '))"
            break
        }
        Write-Log "$($bad.Count) of $($mice.Count) mouse device(s) have problems, but not all. That is not this filter's signature; leaving it alone." 'Warning'
        break
    }

    if ((Get-Date) -ge $deadline) {
        Write-Log 'No mouse devices enumerated before the timeout. Leaving the configuration alone.' 'Warning'
        break
    }
    Start-Sleep -Seconds 5
}

Write-Log 'Guard finished.'
