; Nimbus Adaptive Controller NSIS Installer Script
; Windows Installer Best Practices (modeled after GitConnect Pro)
; Features: wizard UI, admin/user choice, running app check, previous version detection, shortcut options

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "nsDialogs.nsh"

; ---- General ----
!define PRODUCT_NAME "Nimbus Adaptive Controller"
!define PRODUCT_FILENAME "Nimbus-Adaptive-Controller"
!define PRODUCT_EXE "${PRODUCT_FILENAME}-1.4.3.exe"
!define PRODUCT_PUBLISHER "Owen Kent"
!define PRODUCT_VERSION "1.4.3"
!define PRODUCT_GUID "nimbus-adaptive-controller"
!define PRODUCT_UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_GUID}"

; ---- Bundled driver setups ----
; Both are carried inside this installer rather than downloaded: a release asset
; that moves breaks every installer already in the wild, and NSISdl (the only
; download plugin present) cannot fetch an https:// URL at all. Run
; build_tools\fetch_redist.ps1 before makensis; it pins both by SHA-256 and
; checks the publisher signature. Changing a version here means changing it there.
; vJoy is the 2016 2.1.9.1 in Justin Shafer's attestation-signed build on
; purpose: the newer njz3 2.2.1 driver fails to load on Windows 11
; (0xC000009A, njz3/vJoy issue 17). See fetch_redist.ps1.
!define VJOY_SETUP     "vJoySetup-2.1.9.1.exe"
!define VJOY_VERSION   "2.1.9.1"
!define VIGEM_SETUP    "ViGEmBus_1.22.0_x64_x86_arm64.exe"
!define VIGEM_VERSION  "1.22.0"

Name "${PRODUCT_NAME} ${PRODUCT_VERSION}"
OutFile "..\dist\${PRODUCT_FILENAME}-Setup-${PRODUCT_VERSION}.exe"
InstallDir "$PROGRAMFILES64\${PRODUCT_NAME}"
InstallDirRegKey HKLM "${PRODUCT_UNINST_KEY}" "InstallLocation"
RequestExecutionLevel admin  ; Required for Program Files install
SetCompressor /SOLID lzma
BrandingText "${PRODUCT_NAME} v${PRODUCT_VERSION}"

; Variables for shortcut options
Var CreateDesktopShortcut
Var CreateStartMenuShortcut
Var InstallVJoy
Var VJoyInstalled
Var VJoyVersion
Var InstallViGEm
Var ViGEmInstalled
Var ViGEmBroken
Var KeepProfiles

; ---- MUI Settings ----
!define MUI_ICON "Nimbus-Adaptive-Controller.ico"
!define MUI_UNICON "Nimbus-Adaptive-Controller.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Welcome to ${PRODUCT_NAME} Setup"
!define MUI_WELCOMEPAGE_TEXT "This wizard will install ${PRODUCT_NAME} v${PRODUCT_VERSION} on your computer.$\r$\n$\r$\n${PRODUCT_NAME} is a free, open-source modular virtual controller designed for accessibility.$\r$\n$\r$\nRequired virtual controller drivers will be offered on the next page if not already installed.$\r$\n$\r$\nClick Next to continue."

; vJoy detection registry keys — Headsoft original and njz3 fork both covered
!define VJOY_KEY_HEADSOFT "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{8E31F76F-74C3-47F1-9550-E041EEDC5FBB}_is1"
!define VJOY_KEY_FORK    "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{D3B6B8B0-4C9B-4C9B-8A1A-6B3C5E7D8F2A}_is1"
!define VJOY_KEY_PLAIN   "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\vJoy"

; Finish page — MUI2 requires MUI_FINISHPAGE_RUN to be defined (even as a dummy)
; to show the checkbox; the actual launch is overridden by RUN_FUNCTION.
!define MUI_FINISHPAGE_RUN "$INSTDIR\${PRODUCT_EXE}"
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchApplication
!define MUI_FINISHPAGE_RUN_TEXT "Launch ${PRODUCT_NAME}"

; ---- Custom Page for Shortcuts ----
Var Dialog
Var DesktopCheckbox
Var StartMenuCheckbox

Function ShortcutOptionsPage
    !insertmacro MUI_HEADER_TEXT "Shortcut Options" "Choose which shortcuts to create."
    
    nsDialogs::Create 1018
    Pop $Dialog
    ${If} $Dialog == error
        Abort
    ${EndIf}
    
    ${NSD_CreateLabel} 0 0 100% 20u "Select which shortcuts you would like to create:"
    Pop $0
    
    ${NSD_CreateCheckbox} 20u 30u 100% 12u "Create Desktop shortcut"
    Pop $DesktopCheckbox
    ${NSD_Check} $DesktopCheckbox  ; Checked by default
    
    ${NSD_CreateCheckbox} 20u 50u 100% 12u "Create Start Menu shortcut"
    Pop $StartMenuCheckbox
    ${NSD_Check} $StartMenuCheckbox  ; Checked by default
    
    nsDialogs::Show
FunctionEnd

Function ShortcutOptionsLeave
    ${NSD_GetState} $DesktopCheckbox $CreateDesktopShortcut
    ${NSD_GetState} $StartMenuCheckbox $CreateStartMenuShortcut
FunctionEnd

; ---- Custom Page for Driver Installation (vJoy + ViGEmBus) ----
Var VJoyDialog
Var VJoyCheckbox
Var VJoyStatusLabel
Var ViGEmCheckbox
Var ViGEmStatusLabel

; Detection lives in functions because it runs twice: once to draw the page, and
; again after installing, so what the log reports is the actual state of the
; machine rather than an installer exit code.

; Sets $VJoyInstalled (0/1) and $VJoyVersion.
Function DetectVJoy
    Push $0
    StrCpy $VJoyInstalled 0
    StrCpy $VJoyVersion ""
    ; MUST use SetRegView 64: vJoy is a 64-bit install and registers in the
    ; native hive, so 32-bit NSIS would otherwise read WOW6432Node and miss it.
    SetRegView 64
    ReadRegStr $0 HKLM "${VJOY_KEY_HEADSOFT}" "DisplayVersion"
    ${If} $0 == ""
        ReadRegStr $0 HKCU "${VJOY_KEY_HEADSOFT}" "DisplayVersion"
    ${EndIf}
    ${If} $0 == ""
        ReadRegStr $0 HKLM "${VJOY_KEY_FORK}" "DisplayVersion"
    ${EndIf}
    ${If} $0 == ""
        ReadRegStr $0 HKLM "${VJOY_KEY_PLAIN}" "DisplayVersion"
    ${EndIf}
    SetRegView lastused
    ${If} $0 != ""
        StrCpy $VJoyInstalled 1
        StrCpy $VJoyVersion $0
    ${EndIf}
    ; Fallback: the interface DLL on disk, in either Program Files view
    ${If} $VJoyInstalled == 0
        IfFileExists "$PROGRAMFILES64\vJoy\x64\vJoyInterface.dll" 0 +2
            StrCpy $VJoyInstalled 1
    ${EndIf}
    ${If} $VJoyInstalled == 0
        IfFileExists "$PROGRAMFILES\vJoy\x64\vJoyInterface.dll" 0 +2
            StrCpy $VJoyInstalled 1
    ${EndIf}
    ; Files and the uninstall key are not enough either. vJoy's own installer
    ; removes its device node when the device fails to start (measured on the
    ; dev machine on 2026-09-06: a reinstall in the same boot as an uninstall
    ; hit STATUS_INSUFFICIENT_RESOURCES because the old vjoy.sys was still
    ; resident, and vJoyInstall.exe rolled the device back but left everything
    ; else). What remains reports 0 buttons and status UNKN to every client.
    ; The driver's Enum key counts attached devices; require one, so the page
    ; offers a reinstall, which recreates the device.
    ${If} $VJoyInstalled == 1
        SetRegView 64
        ReadRegDWORD $0 HKLM "SYSTEM\CurrentControlSet\Services\vjoy\Enum" "Count"
        SetRegView lastused
        ${If} $0 < 1
            StrCpy $VJoyInstalled 0
        ${EndIf}
    ${EndIf}
    Pop $0
FunctionEnd

; Sets $ViGEmInstalled (0/1).
;
; The service entry alone is not proof. It outlives an uninstall until the next
; reboot, so a machine whose ViGEmBus has been removed still answers "sc query"
; with 0 while every client fails with VIGEM_ERROR_BUS_NOT_FOUND. Measured on
; the dev machine on 2026-09-06 after an MSI removal: service present, PnP
; device gone, vgamepad refusing to open a pad. Trusting the service there would
; skip the install and leave the user with a ViGEmBus that cannot work.
;
; The driver's Enum key counts the devices actually attached to it, which is
; missing or 0 once the bus device is gone, so require both.
Function DetectViGEm
    Push $0
    Push $1
    Push $2
    StrCpy $ViGEmInstalled 0
    StrCpy $ViGEmBroken 0
    nsExec::ExecToStack 'sc query ViGEmBus'
    Pop $0
    Pop $1
    ${If} $0 == 0
        SetRegView 64
        ReadRegDWORD $2 HKLM "SYSTEM\CurrentControlSet\Services\ViGEmBus\Enum" "Count"
        SetRegView lastused
        ${If} $2 >= 1
            StrCpy $ViGEmInstalled 1
        ${Else}
            StrCpy $ViGEmBroken 1
        ${EndIf}
    ${EndIf}
    Pop $2
    Pop $1
    Pop $0
FunctionEnd

Function VJoyOptionsPage
    !insertmacro MUI_HEADER_TEXT "Virtual Controller Drivers" "vJoy and ViGEmBus are required for controller emulation."
    
    nsDialogs::Create 1018
    Pop $VJoyDialog
    ${If} $VJoyDialog == error
        Abort
    ${EndIf}
    
    ; Title
    ${NSD_CreateLabel} 0 0 100% 14u "These drivers let ${PRODUCT_NAME} create virtual game controllers:"
    Pop $0
    
    ; ---- vJoy Section ----
    ${NSD_CreateGroupBox} 0 18u 100% 40u "vJoy (DirectInput controller)"
    Pop $0
    
    Call DetectVJoy
    
    ${If} $VJoyInstalled == 1
        StrCpy $InstallVJoy 0
        ${If} $VJoyVersion != ""
            ${NSD_CreateLabel} 10u 32u 90% 12u "Installed (v$VJoyVersion)"
        ${Else}
            ${NSD_CreateLabel} 10u 32u 90% 12u "Already installed"
        ${EndIf}
        Pop $VJoyStatusLabel
    ${Else}
        StrCpy $InstallVJoy 1
        ${If} $VJoyVersion != ""
            ; Present on paper, but with no device: the reinstall repairs it.
            ${NSD_CreateCheckbox} 10u 32u 90% 12u "Repair vJoy (v$VJoyVersion found, but its device is missing)"
            Pop $VJoyCheckbox
            ${NSD_Check} $VJoyCheckbox
            ${NSD_CreateLabel} 10u 48u 90% 12u "Reinstalling ${VJOY_VERSION} recreates the device. Required for DirectInput profiles"
        ${Else}
            ${NSD_CreateCheckbox} 10u 32u 90% 12u "Install vJoy ${VJOY_VERSION} (recommended)"
            Pop $VJoyCheckbox
            ${NSD_Check} $VJoyCheckbox
            ${NSD_CreateLabel} 10u 48u 90% 12u "Required for flight sim and legacy game profiles"
        ${EndIf}
        Pop $VJoyStatusLabel
    ${EndIf}
    
    ; ---- ViGEmBus Section ----
    ${NSD_CreateGroupBox} 0 64u 100% 40u "ViGEmBus (Xbox 360 controller emulation)"
    Pop $0
    
    Call DetectViGEm
    ${If} $ViGEmInstalled == 1
        StrCpy $InstallViGEm 0
        ${NSD_CreateLabel} 10u 78u 90% 12u "Already installed"
        Pop $ViGEmStatusLabel
    ${Else}
        StrCpy $InstallViGEm 1
        ${If} $ViGEmBroken == 1
            ; The service entry is there but no bus device is attached, which is
            ; what a removed ViGEmBus looks like until the next reboot. Every
            ; client fails with VIGEM_ERROR_BUS_NOT_FOUND; the reinstall repairs it.
            ${NSD_CreateCheckbox} 10u 78u 90% 12u "Repair ViGEmBus (found, but not working)"
            Pop $ViGEmCheckbox
            ${NSD_Check} $ViGEmCheckbox
            ${NSD_CreateLabel} 10u 92u 90% 12u "Reinstalling ${VIGEM_VERSION} restores the bus device. Required for Game Mode"
        ${Else}
            ${NSD_CreateCheckbox} 10u 78u 90% 12u "Install ViGEmBus ${VIGEM_VERSION} (recommended)"
            Pop $ViGEmCheckbox
            ${NSD_Check} $ViGEmCheckbox
            ${NSD_CreateLabel} 10u 92u 90% 12u "Required for Game Mode and Xbox controller profiles"
        ${EndIf}
        Pop $ViGEmStatusLabel
    ${EndIf}
    
    ; Info text
    ${NSD_CreateLabel} 0 110u 100% 24u "Both are open-source drivers used by DS4Windows, Steam and many other tools: vJoy by Shaul Eizikovich and ViGEmBus by Nefarius Software Solutions.$\r$\nThey are included in this installer, so no internet connection is needed. Untick either one to skip it."
    Pop $0
    
    nsDialogs::Show
FunctionEnd

Function VJoyOptionsLeave
    ${If} $VJoyInstalled == 0
        ${NSD_GetState} $VJoyCheckbox $InstallVJoy
    ${EndIf}
    ${If} $ViGEmInstalled == 0
        ${NSD_GetState} $ViGEmCheckbox $InstallViGEm
    ${EndIf}
FunctionEnd

; ---- Custom Page: Keep User Profiles (shown only when upgrading) ----
Var ProfileDialog
Var KeepProfilesRadioYes
Var KeepProfilesRadioNo
Var ProfilesExist

Function ProfilesPage
    ; Only show this page if user data exists in %APPDATA%
    IfFileExists "$APPDATA\ProjectNimbus\*.*" 0 skipPage
    StrCpy $ProfilesExist 1
    
    !insertmacro MUI_HEADER_TEXT "Saved Profiles & Settings" "Your saved controller profiles were found."
    
    nsDialogs::Create 1018
    Pop $ProfileDialog
    ${If} $ProfileDialog == error
        Abort
    ${EndIf}
    
    ${NSD_CreateLabel} 0 0 100% 30u "${PRODUCT_NAME} found existing saved profiles and settings at:$\r$\n$APPDATA\ProjectNimbus"
    Pop $0
    
    ${NSD_CreateLabel} 0 38u 100% 12u "What would you like to do with your saved profiles?"
    Pop $0
    
    ${NSD_CreateRadioButton} 20u 58u 100% 12u "Keep my profiles and settings (recommended)"
    Pop $KeepProfilesRadioYes
    ${NSD_Check} $KeepProfilesRadioYes
    
    ${NSD_CreateRadioButton} 20u 76u 100% 12u "Remove profiles and start fresh"
    Pop $KeepProfilesRadioNo
    
    ${NSD_CreateLabel} 0 100u 100% 30u "Keeping profiles lets you continue right where you left off.$\r$\nYour customizations will survive future upgrades too."
    Pop $0
    
    nsDialogs::Show
    Goto endPage
    skipPage:
        StrCpy $ProfilesExist 0
        StrCpy $KeepProfiles 1  ; default: keep (nothing to delete anyway)
    endPage:
FunctionEnd

Function ProfilesPageLeave
    ${If} $ProfilesExist == 1
        ${NSD_GetState} $KeepProfilesRadioYes $KeepProfiles
    ${EndIf}
FunctionEnd

; ---- Pages ----
!insertmacro MUI_PAGE_WELCOME
Page custom VJoyOptionsPage VJoyOptionsLeave
!insertmacro MUI_PAGE_DIRECTORY
Page custom ShortcutOptionsPage ShortcutOptionsLeave
Page custom ProfilesPage ProfilesPageLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; Finish-page launch function — must be defined AFTER MUI_PAGE_FINISH macro
Function LaunchApplication
    ; System::Call ShellExecuteW launches at normal user privilege,
    ; not inheriting the installer's elevated admin token.
    System::Call 'shell32::ShellExecuteW(i $HWNDPARENT, w "open", w "$INSTDIR\${PRODUCT_EXE}", w "", w "$INSTDIR", i 1)'
FunctionEnd

; ---- Init: check if already running ----
Function .onInit
    ; Bring installer to front (important after UAC elevation)
    BringToFront
    
    ; Initialize shortcut options to checked (1 = checked in NSIS)
    StrCpy $CreateDesktopShortcut 1
    StrCpy $CreateStartMenuShortcut 1

    ; A silent install (/S) skips every custom page, so nothing would set the
    ; driver choices and a scripted deployment would end up with the app and no
    ; drivers. Default to installing whichever driver is missing, and leave the
    ; ones already present alone. Every MessageBox below carries /SD for the
    ; same reason: without it a silent install stops on a dialog nobody sees.
    ${If} ${Silent}
        Call DetectVJoy
        Call DetectViGEm
        ${If} $VJoyInstalled == 1
            StrCpy $InstallVJoy 0
        ${Else}
            StrCpy $InstallVJoy 1
        ${EndIf}
        ${If} $ViGEmInstalled == 1
            StrCpy $InstallViGEm 0
        ${Else}
            StrCpy $InstallViGEm 1
        ${EndIf}
    ${EndIf}
    
    ; Check if Nimbus Adaptive Controller is currently running
    nsExec::ExecToStack 'cmd /c tasklist /FI "IMAGENAME eq ${PRODUCT_EXE}" /NH | findstr /I "Nimbus-Adaptive"'
    Pop $0
    ${If} $0 == 0
        MessageBox MB_OKCANCEL|MB_ICONEXCLAMATION "${PRODUCT_NAME} is currently running.$\r$\n$\r$\nClick OK to close it and continue, or Cancel to abort." /SD IDOK IDOK closeApp IDCANCEL abortInstall
        abortInstall:
            Abort
        closeApp:
            nsExec::ExecToLog 'taskkill /F /IM "${PRODUCT_EXE}"'
            Sleep 1500
    ${EndIf}

    ; Check for previous installation in HKCU (per-user install)
    ReadRegStr $0 HKCU "${PRODUCT_UNINST_KEY}" "UninstallString"
    ReadRegStr $2 HKCU "${PRODUCT_UNINST_KEY}" "InstallLocation"
    ${If} $0 != ""
    ${AndIf} $2 != ""
        ReadRegStr $1 HKCU "${PRODUCT_UNINST_KEY}" "DisplayVersion"
        MessageBox MB_YESNO|MB_ICONQUESTION "A previous version of ${PRODUCT_NAME} (v$1) was found at:$\r$\n$2$\r$\n$\r$\nWould you like to remove it before installing the new version?$\r$\n(Recommended: Yes)" /SD IDYES IDYES removeUserPrev IDNO skipUserPrev
        removeUserPrev:
            ExecWait '"$0" /S'
            Sleep 2000
        skipUserPrev:
    ${EndIf}

    ; Check for previous installation in HKLM (per-machine install)
    ReadRegStr $0 HKLM "${PRODUCT_UNINST_KEY}" "UninstallString"
    ReadRegStr $2 HKLM "${PRODUCT_UNINST_KEY}" "InstallLocation"
    ${If} $0 != ""
    ${AndIf} $2 != ""
        ReadRegStr $1 HKLM "${PRODUCT_UNINST_KEY}" "DisplayVersion"
        MessageBox MB_YESNO|MB_ICONQUESTION "A system-wide installation of ${PRODUCT_NAME} (v$1) was found at:$\r$\n$2$\r$\n$\r$\nWould you like to remove it before installing the new version?$\r$\n(Recommended: Yes)" /SD IDYES IDYES removeMachinePrev IDNO skipMachinePrev
        removeMachinePrev:
            ExecWait '"$0" /S'
            Sleep 2000
        skipMachinePrev:
    ${EndIf}
FunctionEnd

; ---- Install Section ----
Section "Install"
    SetOutPath "$INSTDIR"

    ; Install main executable
    File "..\dist\${PRODUCT_EXE}"  ; PyInstaller output: Project-Nimbus-1.3.1.exe

    ; Create uninstaller
    WriteUninstaller "$INSTDIR\Uninstall.exe"

    ; Registry entries for Add/Remove Programs (HKCU for per-user)
    WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayName" "${PRODUCT_NAME}"
    WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayVersion" "${PRODUCT_VERSION}"
    WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"
    WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "UninstallString" "$INSTDIR\Uninstall.exe"
    WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayIcon" "$INSTDIR\${PRODUCT_EXE}"
    WriteRegDWORD HKLM "${PRODUCT_UNINST_KEY}" "NoModify" 1
    WriteRegDWORD HKLM "${PRODUCT_UNINST_KEY}" "NoRepair" 1

    ; Estimate installed size
    ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
    IntFmt $0 "0x%08X" $0
    WriteRegDWORD HKLM "${PRODUCT_UNINST_KEY}" "EstimatedSize" $0

    ; Register nimbus:// custom URL scheme for OAuth callback
    WriteRegStr HKCR "nimbus" "" "URL:Nimbus Adaptive Controller"
    WriteRegStr HKCR "nimbus" "URL Protocol" ""
    WriteRegStr HKCR "nimbus\DefaultIcon" "" "$INSTDIR\${PRODUCT_EXE},0"
    WriteRegStr HKCR "nimbus\shell\open\command" "" '"$INSTDIR\${PRODUCT_EXE}" "%1"'

    ; Create shortcuts based on user selection
    ; NOTE: icon argument omitted — NSIS silently drops shortcuts when icon path contains spaces
    ${If} $CreateStartMenuShortcut == 1
        CreateDirectory "$SMPROGRAMS\${PRODUCT_NAME}"
        CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk" "$INSTDIR\${PRODUCT_EXE}"
        CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall ${PRODUCT_NAME}.lnk" "$INSTDIR\Uninstall.exe"
    ${EndIf}
    
    ${If} $CreateDesktopShortcut == 1
        CreateShortCut "$DESKTOP\${PRODUCT_NAME}.lnk" "$INSTDIR\${PRODUCT_EXE}"
    ${EndIf}
    
    ; ---- Driver installation ----
    ; Both setups are bundled (see the defines at the top and
    ; build_tools\fetch_redist.ps1). $PLUGINSDIR is wiped when the installer
    ; exits, so a setup is copied to $INSTDIR\drivers only when it fails and the
    ; user may need to run it by hand.
    ;
    ; Success is judged by detecting the driver afterwards, not by the exit code:
    ; these are third-party setups and their codes are not all documented. Exit
    ; codes 3010 and 1641 mean "installed, needs a restart", which is not failure.
    ${If} $InstallVJoy == 1
    ${OrIf} $InstallViGEm == 1
        InitPluginsDir
        SetOutPath "$PLUGINSDIR"
    ${EndIf}

    ${If} $InstallVJoy == 1
        DetailPrint "Installing vJoy ${VJOY_VERSION}..."
        File "redist\${VJOY_SETUP}"
        ExecWait '"$PLUGINSDIR\${VJOY_SETUP}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART' $0
        StrCpy $2 0
        ${If} $0 == 0
        ${OrIf} $0 == 3010
        ${OrIf} $0 == 1641
            StrCpy $2 1
        ${EndIf}
        ${If} $0 == 3010
        ${OrIf} $0 == 1641
            SetRebootFlag true
        ${EndIf}
        Call DetectVJoy
        ${If} $VJoyInstalled == 1
            DetailPrint "vJoy installed"
            ; Configure device 1 the way Nimbus profiles expect: 8 axes, 128 buttons.
            ; vJoy is a 64-bit install, so this is $PROGRAMFILES64; the 32-bit
            ; installer's $PROGRAMFILES points at Program Files (x86), where vJoy
            ; never lands, which is why this step used to do nothing.
            StrCpy $3 "$PROGRAMFILES64\vJoy\x64\vJoyConfig.exe"
            ${IfNot} ${FileExists} "$3"
                StrCpy $3 "$PROGRAMFILES\vJoy\x64\vJoyConfig.exe"
            ${EndIf}
            ${If} ${FileExists} "$3"
                nsExec::ExecToLog '"$3" 1 -f -a x y z rx ry rz sl0 sl1 -b 128'
                Pop $1
                ${If} $1 == 0
                    DetailPrint "vJoy device 1 configured (8 axes, 128 buttons)"
                ${Else}
                    DetailPrint "vJoy device 1 not reconfigured (code $1); Nimbus will use the device as it is"
                ${EndIf}
            ${Else}
                DetailPrint "vJoyConfig.exe not found; leaving the vJoy device as it is"
            ${EndIf}
        ${ElseIf} $2 == 1
            ; Setup reported success but the driver is not visible yet.
            SetRebootFlag true
            DetailPrint "vJoy installed; restart Windows to finish"
        ${Else}
            DetailPrint "vJoy setup failed (exit code $0)"
            CreateDirectory "$INSTDIR\drivers"
            CopyFiles /SILENT "$PLUGINSDIR\${VJOY_SETUP}" "$INSTDIR\drivers"
            MessageBox MB_YESNO|MB_ICONEXCLAMATION "The vJoy driver did not install (code $0).$\r$\n$\r$\nNimbus needs it for DirectInput profiles. A copy of the setup was saved here:$\r$\n$INSTDIR\drivers\${VJOY_SETUP}$\r$\n$\r$\nRun it now with its own installer window?" /SD IDNO IDYES vjoyManual IDNO vjoyDone
            vjoyManual:
                ExecWait '"$INSTDIR\drivers\${VJOY_SETUP}"'
                Call DetectVJoy
            vjoyDone:
        ${EndIf}
    ${EndIf}

    ${If} $InstallViGEm == 1
        DetailPrint "Installing ViGEmBus ${VIGEM_VERSION}..."
        File "redist\${VIGEM_SETUP}"
        ; Advanced Installer bootstrapper: /exenoui hides its own UI and passes
        ; the rest to msiexec.
        ExecWait '"$PLUGINSDIR\${VIGEM_SETUP}" /exenoui /qn /norestart' $0
        StrCpy $2 0
        ${If} $0 == 0
        ${OrIf} $0 == 3010
        ${OrIf} $0 == 1641
            StrCpy $2 1
        ${EndIf}
        ${If} $0 == 3010
        ${OrIf} $0 == 1641
            SetRebootFlag true
        ${EndIf}
        Call DetectViGEm
        ${If} $ViGEmInstalled == 1
            DetailPrint "ViGEmBus installed"
        ${ElseIf} $2 == 1
            SetRebootFlag true
            DetailPrint "ViGEmBus installed; restart Windows to finish"
        ${Else}
            DetailPrint "ViGEmBus setup failed (exit code $0)"
            CreateDirectory "$INSTDIR\drivers"
            CopyFiles /SILENT "$PLUGINSDIR\${VIGEM_SETUP}" "$INSTDIR\drivers"
            MessageBox MB_YESNO|MB_ICONEXCLAMATION "The ViGEmBus driver did not install (code $0).$\r$\n$\r$\nGame Mode and the Xbox controller profiles need it. A copy of the setup was saved here:$\r$\n$INSTDIR\drivers\${VIGEM_SETUP}$\r$\n$\r$\nRun it now with its own installer window?" /SD IDNO IDYES vigemManual IDNO vigemDone
            vigemManual:
                ExecWait '"$INSTDIR\drivers\${VIGEM_SETUP}"'
                Call DetectViGEm
            vigemDone:
        ${EndIf}
    ${EndIf}

    ; One plain summary, so a user who skipped or lost a driver knows before the
    ; app tells them in its own way.
    Call DetectVJoy
    Call DetectViGEm
    ${If} $VJoyInstalled == 0
    ${OrIf} $ViGEmInstalled == 0
        StrCpy $1 ""
        ${If} $VJoyInstalled == 0
            StrCpy $1 "$1$\r$\n  vJoy is missing. DirectInput profiles will not work."
        ${EndIf}
        ${If} $ViGEmInstalled == 0
            StrCpy $1 "$1$\r$\n  ViGEmBus is missing. Game Mode and Xbox controller profiles will not work."
        ${EndIf}
        DetailPrint "Driver check: one or more drivers are missing"
        MessageBox MB_OK|MB_ICONINFORMATION "${PRODUCT_NAME} is installed, but not every controller driver is present:$\r$\n$1$\r$\n$\r$\nRun this installer again at any time to add them. Nimbus starts and runs either way." /SD IDOK
    ${Else}
        DetailPrint "Driver check: vJoy and ViGEmBus are both present"
    ${EndIf}
SectionEnd

; ---- Uninstall Section ----
Section "Uninstall"
    ; Remove application files from install directory
    Delete "$INSTDIR\${PRODUCT_EXE}"
    Delete "$INSTDIR\Uninstall.exe"
    Delete "$INSTDIR\controller_config.json"
    RMDir /r "$INSTDIR\profiles"
    ; Any driver setup left behind for the user after a failed silent install.
    ; vJoy and ViGEmBus themselves are deliberately left installed: other
    ; software (DS4Windows, Steam, other mappers) shares them.
    RMDir /r "$INSTDIR\drivers"
    RMDir "$INSTDIR"

    ; Remove shortcuts
    Delete "$DESKTOP\${PRODUCT_NAME}.lnk"
    Delete "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk"
    Delete "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall ${PRODUCT_NAME}.lnk"
    RMDir "$SMPROGRAMS\${PRODUCT_NAME}"

    ; Remove registry entries
    DeleteRegKey HKLM "${PRODUCT_UNINST_KEY}"

    ; Remove nimbus:// custom URL scheme
    DeleteRegKey HKCR "nimbus"

    ; Remove user data only if user chose to (KeepProfiles == 0 means "remove")
    ${If} $KeepProfiles == 0
        RMDir /r "$APPDATA\ProjectNimbus"
    ${EndIf}
SectionEnd
