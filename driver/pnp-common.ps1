<#
.SYNOPSIS
    Helpers shared by install-dev.ps1 and uninstall-dev.ps1 (dot-sourced).
#>

function Restart-Mice {
    <#
    .SYNOPSIS
        Restart every present mouse so class filters attach or detach.

    .DESCRIPTION
        There is no Restart-PnpDevice cmdlet: the PnpDevice module only has
        Get, Enable and Disable. "pnputil /restart-device" (Windows 10 2004
        and later) performs the query-remove and re-enumerate that reloads
        the filter stack; Disable-PnpDevice followed by Enable-PnpDevice is
        the fallback for older builds. The mouse drops out for a second or
        two while this runs; the keyboard is untouched.
    #>
    Get-PnpDevice -Class Mouse -PresentOnly -ErrorAction SilentlyContinue | ForEach-Object {
        $dev = $_
        Write-Host "  restarting $($dev.FriendlyName)"
        pnputil /restart-device "$($dev.InstanceId)" | Out-Null
        if ($LASTEXITCODE -eq 0) { return }
        if ($LASTEXITCODE -eq 3010) {
            Write-Warning '    Windows says this device needs a reboot to restart; the filter change applies after the reboot.'
            return
        }
        Write-Warning "    pnputil /restart-device exited $LASTEXITCODE; trying Disable-PnpDevice / Enable-PnpDevice"
        try {
            $dev | Disable-PnpDevice -Confirm:$false -ErrorAction Stop
            $dev | Enable-PnpDevice -Confirm:$false -ErrorAction Stop
        } catch {
            Write-Warning "    restart failed: $($_.Exception.Message)"
        }
    }
    Start-Sleep -Seconds 3
}
