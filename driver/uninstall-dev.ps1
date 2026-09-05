<#
.SYNOPSIS
    Removes the development install of the Nimbus Mouse Filter.
    Run from an elevated PowerShell.

.DESCRIPTION
    Removes "nimbus_moufilter" from the mouse class UpperFilters list, restarts
    the mouse devices so the filter detaches (the driver unloads when the last
    filtered mouse restarts), deletes the service, deletes the file, and
    removes any nimbus_moufilter.inf package that pnputil left in the Driver
    Store (an INF install points the service there instead of at
    System32\drivers, which install-dev.ps1 refuses to update).
#>
param([switch]$NoRestart)

$ErrorActionPreference = 'Continue'
$service = 'nimbus_moufilter'
$classKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4D36E96F-E325-11CE-BFC1-08002BE10318}'

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw 'Run this from an elevated PowerShell.' }

$current = (Get-ItemProperty -Path $classKey -Name UpperFilters -ErrorAction SilentlyContinue).UpperFilters
if ($current -and ($current -contains $service)) {
    $remaining = @($current | Where-Object { $_ -ne $service })
    # mouclass itself is normally in this list; it must stay, and the value is never deleted.
    if ($remaining -notcontains 'mouclass') { $remaining = @('mouclass') + $remaining }
    Write-Host "Setting mouse class UpperFilters = $($remaining -join ', ')"
    Set-ItemProperty -Path $classKey -Name UpperFilters -Value ([string[]]$remaining) -Type MultiString
} else {
    Write-Host "UpperFilters does not contain $service"
}

. (Join-Path $PSScriptRoot 'pnp-common.ps1')   # Restart-Mice

if (-not $NoRestart) {
    Write-Host 'Restarting mouse devices so the filter detaches'
    Restart-Mice
}

sc.exe stop $service 2>$null | Out-Null
sc.exe delete $service | Out-Null
Write-Host "Service $service deleted (exit $LASTEXITCODE)"

$dest = Join-Path $env:SystemRoot 'System32\drivers\nimbus_moufilter.sys'
if (Test-Path $dest) {
    try { Remove-Item $dest -Force -ErrorAction Stop; Write-Host "Deleted $dest" }
    catch { Write-Warning "Could not delete $dest yet (still loaded?). It goes away after a reboot." }
}

# A pnputil / INF install leaves a package in the Driver Store whose copy of
# the .sys would keep loading after a rebuild. Best effort: the DISM module
# is standard on Windows 10/11 but the enumeration can take a few seconds.
try {
    $packages = @(Get-WindowsDriver -Online -ErrorAction Stop | Where-Object { $_.OriginalFileName -like '*nimbus_moufilter.inf' })
    foreach ($pkg in $packages) {
        Write-Host "Removing Driver Store package $($pkg.Driver) ($($pkg.OriginalFileName))"
        pnputil /delete-driver $pkg.Driver /uninstall /force | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Warning "pnputil /delete-driver $($pkg.Driver) exited $LASTEXITCODE" }
    }
} catch {
    Write-Warning "Could not check the Driver Store ($($_.Exception.Message)). If the driver was ever added with pnputil, find it with 'pnputil /enum-drivers' and remove it with 'pnputil /delete-driver oemNN.inf /uninstall'."
}
Write-Host 'Done.'
