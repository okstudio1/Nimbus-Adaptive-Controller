import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Controls.Basic 2.15 as Basic
import QtQuick.Layouts 1.15
import QtQuick.Shapes 1.15
import "components" as Comp
import "layouts" as Layouts

ApplicationWindow {
    id: root
    visible: true
    width: 1024
    height: 600
    minimumWidth: 700
    minimumHeight: 450
    title: "Nimbus Adaptive Controller"
    background: Rectangle { color: "black" }

    // Scale factor bound to Python bridge; default 1.0
    property real scaleFactor: controller ? controller.scaleFactor : 1.0
    
    // Current profile and layout type
    property string currentProfile: controller ? controller.getCurrentProfile() : "flight_simulator"
    property string layoutType: controller ? controller.getLayoutType() : "flight_sim"
    
    // Guard: prevents Edit Layout menu from appearing during init
    property bool _appReady: false

    // Game Mode state
    property bool gameModeActive: false
    property int gameModeHwnd: 0
    property string gameModeTitle: ""

    // Controller monitor bar
    property bool controllerMonitorVisible: false

    // Output device mode: "vjoy" or "vigem"
    property string outputMode: controller ? controller.getOutputMode() : "vjoy"

    // Timer to delay _appReady until menu bar is fully stable
    Timer {
        id: appReadyTimer
        interval: 500
        repeat: false
        onTriggered: root._appReady = true
    }

    // Track available profiles for dynamic updates
    property var availableProfiles: controller ? controller.getAvailableProfiles() : []
    property var recentProfiles: []
    
    // Initialize window reference for game focus mode
    Component.onCompleted: {
        if (controller) {
            controller.setWindow(root)
        }
        appReadyTimer.start()
    }
    
    // Global mouse tracker for game focus mode
    // This detects when user starts/stops interacting with the window
    MouseArea {
        id: globalMouseTracker
        anchors.fill: parent
        z: -1  // Behind everything so it doesn't block other mouse areas
        propagateComposedEvents: true
        acceptedButtons: Qt.AllButtons
        
        onPressed: function(mouse) {
            if (controller) controller.onMousePressed()
            mouse.accepted = false  // Let event propagate to children
        }
        
        onReleased: function(mouse) {
            if (controller) controller.onMouseReleased()
            mouse.accepted = false
        }
    }
    
    // Update when profile changes
    Connections {
        target: controller
        function onProfileChanged(profileId) {
            root.currentProfile = profileId
        }
        function onLayoutTypeChanged(newLayoutType) {
            root.layoutType = newLayoutType
        }
        function onProfilesListChanged() {
            root.availableProfiles = controller.getAvailableProfiles()
        }
        function onRecentProfilesChanged() {
            root.recentProfiles = controller.getRecentProfiles()
        }
        function onProfileSaved(success) {
            if (success) {
                saveNotification.show("Profile saved successfully")
            } else {
                saveNotification.show("Failed to save profile")
            }
        }
        function onNoFocusModeChanged(enabled) {
            // Update menu checkbox when mode changes
            noFocusModeItem.checked = enabled
        }
        function onMouseIsolationChanged(active) {
            mouseIsolationItem.checked = active
        }
        function onControllerModeChanged(active) {
            root.gameModeActive = active
        }
        function onOutputModeChanged(mode) {
            root.outputMode = mode
        }
    }
    
    // Simple notification popup
    Rectangle {
        id: saveNotification
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: 50
        width: notificationLabel.width + 40
        height: 40
        radius: 8
        color: "#333"
        border.color: "#555"
        border.width: 1
        opacity: 0
        z: 1000
        
        function show(message) {
            notificationLabel.text = message
            notificationAnimation.start()
        }
        
        Label {
            id: notificationLabel
            anchors.centerIn: parent
            color: "white"
            font.pixelSize: 14
        }
        
        SequentialAnimation {
            id: notificationAnimation
            NumberAnimation { target: saveNotification; property: "opacity"; to: 1; duration: 200 }
            PauseAnimation { duration: 2000 }
            NumberAnimation { target: saveNotification; property: "opacity"; to: 0; duration: 500 }
        }
    }

    // New Profile — combined name entry + optional save-current toggle
    Dialog {
        id: newProfileDialog
        title: "New Profile"
        modal: true
        anchors.centerIn: parent
        width: 400
        height: 310

        background: Rectangle { color: "#2a2a2a"; border.color: "#555"; radius: 6 }

        contentItem: ColumnLayout {
            spacing: 12
            anchors.fill: parent
            anchors.margins: 16

            Label {
                text: "Profile Name:"
                color: "#ccc"
                font.pixelSize: 13
            }
            Basic.TextField {
                id: newProfileNameField
                Layout.fillWidth: true
                placeholderText: "Enter profile name..."
                color: "white"
                font.pixelSize: 14
                background: Rectangle {
                    color: "#1a1a1a"
                    border.color: newProfileNameField.activeFocus ? "#4a9eff" : "#555"
                    border.width: 1
                    radius: 4
                }
            }

            Label {
                text: "Description (optional):"
                color: "#ccc"
                font.pixelSize: 13
            }
            Basic.TextField {
                id: newProfileDescField
                Layout.fillWidth: true
                placeholderText: "Enter description..."
                color: "white"
                font.pixelSize: 13
                background: Rectangle {
                    color: "#1a1a1a"
                    border.color: newProfileDescField.activeFocus ? "#4a9eff" : "#555"
                    border.width: 1
                    radius: 4
                }
            }

            Basic.CheckBox {
                id: saveCurrentCheck
                text: "Save \"" + root.currentProfile + "\" before switching"
                checked: true
                contentItem: Label {
                    text: saveCurrentCheck.text
                    color: "#bbb"
                    font.pixelSize: 12
                    leftPadding: saveCurrentCheck.indicator.width + saveCurrentCheck.spacing
                    verticalAlignment: Text.AlignVCenter
                }
            }

            Item { Layout.fillHeight: true }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Item { Layout.fillWidth: true }
                Basic.Button {
                    text: "Create"
                    enabled: newProfileNameField.text.trim().length > 0
                    onClicked: {
                        var name = newProfileNameField.text.trim()
                        var desc = newProfileDescField.text.trim()
                        newProfileDialog.close()
                        Qt.callLater(function() {
                            if (saveCurrentCheck.checked && controller)
                                controller.saveCurrentProfile()
                            var newId = controller ? controller.createProfileAs(name, desc) : ""
                            if (newId !== "") {
                                saveNotification.show("Profile '" + name + "' created")
                                if (controller) controller.switchProfile(newId)
                            } else {
                                saveNotification.show("Failed to create profile")
                            }
                        })
                    }
                    contentItem: Label { text: parent.text; color: "white"; horizontalAlignment: Text.AlignHCenter }
                    background: Rectangle { color: parent.hovered ? "#1a6adf" : "#1255b8"; radius: 4 }
                }
                Basic.Button {
                    text: "Cancel"
                    onClicked: newProfileDialog.close()
                    contentItem: Label { text: parent.text; color: "#aaa"; horizontalAlignment: Text.AlignHCenter }
                    background: Rectangle { color: parent.hovered ? "#333" : "#222"; radius: 4 }
                }
            }
        }

        onOpened: {
            newProfileNameField.text = ""
            newProfileDescField.text = ""
            saveCurrentCheck.checked = true
            newProfileNameField.forceActiveFocus()
        }
    }

    // Save Profile As Dialog
    Dialog {
        id: saveAsDialog
        title: "Save Profile As"
        modal: true
        anchors.centerIn: parent
        width: 400
        height: 280
        
        background: Rectangle {
            color: "#2a2a2a"
            border.color: "#555"
            border.width: 1
            radius: 8
        }
        
        header: Label {
            text: saveAsDialog.title
            color: "white"
            font.pixelSize: 16
            font.bold: true
            padding: 16
            background: Rectangle { color: "#333"; radius: 8 }
        }
        
        contentItem: Column {
            spacing: 16
            padding: 16
            
            Column {
                spacing: 6
                width: parent.width - 32
                
                Label {
                    text: "Profile Name:"
                    color: "#ccc"
                    font.pixelSize: 13
                }
                Basic.TextField {
                    id: profileNameField
                    width: parent.width
                    placeholderText: "Enter profile name..."
                    color: "white"
                    font.pixelSize: 14
                    background: Rectangle {
                        color: "#1a1a1a"
                        border.color: profileNameField.activeFocus ? "#4a9eff" : "#555"
                        border.width: 1
                        radius: 4
                    }
                }
            }
            
            Column {
                spacing: 6
                width: parent.width - 32
                
                Label {
                    text: "Description (optional):"
                    color: "#ccc"
                    font.pixelSize: 13
                }
                Basic.TextField {
                    id: profileDescField
                    width: parent.width
                    placeholderText: "Enter description..."
                    color: "white"
                    font.pixelSize: 14
                    background: Rectangle {
                        color: "#1a1a1a"
                        border.color: profileDescField.activeFocus ? "#4a9eff" : "#555"
                        border.width: 1
                        radius: 4
                    }
                }
            }
        }
        
        footer: Row {
            spacing: 12
            padding: 16
            layoutDirection: Qt.RightToLeft
            
            Basic.Button {
                text: "Save"
                enabled: profileNameField.text.trim().length > 0
                onClicked: {
                    var newId = controller.createProfileAs(
                        profileNameField.text.trim(),
                        profileDescField.text.trim()
                    )
                    if (newId !== "") {
                        saveNotification.show("Profile '" + profileNameField.text.trim() + "' created")
                        controller.switchProfile(newId)
                    } else {
                        saveNotification.show("Failed to create profile")
                    }
                    profileNameField.text = ""
                    profileDescField.text = ""
                    saveAsDialog.close()
                }
                contentItem: Label {
                    text: parent.text
                    color: parent.enabled ? "white" : "#888"
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    implicitWidth: 80
                    implicitHeight: 32
                    color: parent.enabled ? (parent.down ? "#2563eb" : "#3b82f6") : "#444"
                    radius: 4
                }
            }
            
            Basic.Button {
                text: "Cancel"
                onClicked: {
                    profileNameField.text = ""
                    profileDescField.text = ""
                    saveAsDialog.close()
                }
                contentItem: Label {
                    text: parent.text
                    color: "#ccc"
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    implicitWidth: 80
                    implicitHeight: 32
                    color: parent.down ? "#444" : "#333"
                    border.color: "#555"
                    border.width: 1
                    radius: 4
                }
            }
        }
        
        onOpened: {
            profileNameField.text = ""
            profileDescField.text = ""
            profileNameField.forceActiveFocus()
        }
    }

    // Menus (safe dark mode for top bar only)
    menuBar: MenuBar {
        background: Rectangle { color: "#2a2a2a" }
        delegate: MenuBarItem {
            id: control
            implicitHeight: 30
            contentItem: Label {
                text: control.text
                color: "white"
                verticalAlignment: Text.AlignVCenter
                horizontalAlignment: Text.AlignHCenter
                leftPadding: 10
                rightPadding: 10
            }
            background: Rectangle {
                color: control.hovered ? "#3a3a3a" : "#2a2a2a"
            }
        }

        Menu {
            id: fileMenu
            title: qsTr("File")

            MenuItem {
                text: qsTr("New Profile...")
                onTriggered: {
                    fileMenu.close()
                    Qt.callLater(function() { newProfileDialog.open() })
                }
            }

            MenuSeparator {}

            // Profile submenu
            Menu {
                id: profileMenu
                title: qsTr("Profile")
                
                Instantiator {
                    id: profileInstantiator
                    model: root.availableProfiles
                    delegate: MenuItem {
                        text: modelData.name
                        checkable: true
                        checked: root.currentProfile === modelData.id
                        onTriggered: {
                            fileMenu.close()
                            Qt.callLater(function() {
                                if (controller) controller.switchProfile(modelData.id)
                            })
                        }
                    }
                    onObjectAdded: function(index, object) { profileMenu.insertItem(index, object) }
                    onObjectRemoved: function(index, object) { profileMenu.removeItem(object) }
                }
            }
            
            // Recent Profiles submenu
            Menu {
                id: recentProfilesMenu
                title: qsTr("Recent Profiles")
                enabled: root.recentProfiles.length > 0

                Instantiator {
                    model: root.recentProfiles
                    delegate: MenuItem {
                        text: modelData.name
                        checkable: true
                        checked: root.currentProfile === modelData.id
                        onTriggered: {
                            recentProfilesMenu.close()
                            fileMenu.close()
                            Qt.callLater(function() {
                                if (controller) controller.switchProfile(modelData.id)
                            })
                        }
                    }
                    onObjectAdded: function(index, object) { recentProfilesMenu.insertItem(index, object) }
                    onObjectRemoved: function(index, object) { recentProfilesMenu.removeItem(object) }
                }
            }

            MenuSeparator {}

            MenuItem {
                text: qsTr("Save Profile")
                onTriggered: {
                    fileMenu.close()
                    Qt.callLater(function() {
                        if (controller) controller.saveCurrentProfile()
                    })
                }
            }
            MenuItem {
                text: qsTr("Save Profile As...")
                onTriggered: {
                    fileMenu.close()
                    Qt.callLater(function() {
                        saveAsDialog.open()
                    })
                }
            }
            MenuItem {
                text: qsTr("Reset Profile to Defaults")
                enabled: controller ? controller.isBuiltinProfile(root.currentProfile) : false
                onTriggered: {
                    fileMenu.close()
                    Qt.callLater(function() {
                        if (controller) {
                            controller.resetProfile(root.currentProfile)
                            saveNotification.show("Profile reset to defaults")
                        }
                    })
                }
            }
            
            MenuSeparator {}
            
            MenuItem {
                text: qsTr("Open Profiles Folder...")
                onTriggered: {
                    fileMenu.close()
                    Qt.callLater(function() {
                        if (controller) controller.openProfilesFolder()
                    })
                }
            }
            
            MenuSeparator {}
            MenuItem { text: qsTr("Exit"); onTriggered: Qt.quit() }
        }
        
        // Edit Layout — top-level menu, visible only for custom layout
        // Deferred via Loader to prevent menu appearing during startup
        Loader {
            active: root._appReady && root.layoutType === "custom"
            sourceComponent: Menu {
                id: editLayoutMenu
                title: qsTr("Edit Layout")
                Component.onCompleted: {
                    // Insert into menu bar after Settings
                    menuBar.insertMenu(3, editLayoutMenu)
                }
                Component.onDestruction: {
                    menuBar.removeMenu(editLayoutMenu)
                }
                MenuItem {
                    text: layoutLoader.item && layoutLoader.item.editMode ? qsTr("✓ Done Editing Layout") : qsTr("Edit Layout...")
                    onTriggered: {
                        editLayoutMenu.close()
                        Qt.callLater(function(){
                            if (layoutLoader.item) {
                                layoutLoader.item.editMode = !layoutLoader.item.editMode
                                if (!layoutLoader.item.editMode) layoutLoader.item._saveLayout()
                            }
                        })
                    }
                }
            }
        }

        // Settings menu
        Menu {
            id: settingsMenu
            title: qsTr("Settings")
            
            Menu {
                id: outputDeviceMenu
                title: qsTr("Output Device")
                
                MenuItem {
                    id: outputVjoyItem
                    text: controller ? controller.getOutputModeLabel("vjoy") : qsTr("vJoy (DirectInput)")
                    checkable: true
                    checked: root.outputMode === "vjoy"
                    onTriggered: {
                        outputDeviceMenu.close()
                        settingsMenu.close()
                        Qt.callLater(function(){ if (controller) controller.setOutputMode("vjoy"); })
                    }
                }
                MenuItem {
                    id: outputVigemItem
                    text: controller ? controller.getOutputModeLabel("vigem") : qsTr("ViGEm Xbox 360 (XInput)")
                    checkable: true
                    checked: root.outputMode === "vigem"
                    enabled: controller ? controller.isVigemAvailable() : false
                    onTriggered: {
                        outputDeviceMenu.close()
                        settingsMenu.close()
                        Qt.callLater(function(){ if (controller) controller.setOutputMode("vigem"); })
                    }
                }
            }
            
            MenuSeparator {}
            
            MenuItem { 
                text: qsTr("Axis Configuration...")
                visible: root.layoutType !== "custom"
                height: visible ? implicitHeight : 0
                onTriggered: { 
                    settingsMenu.close()
                    Qt.callLater(function(){ if (controller) controller.openAxisSettings(); })
                } 
            }
            MenuItem { 
                text: qsTr("Button Modes...")
                visible: root.layoutType !== "custom"
                height: visible ? implicitHeight : 0
                onTriggered: { 
                    settingsMenu.close()
                    Qt.callLater(function(){ if (controller) controller.openButtonSettings(); })
                } 
            }
        }
        
        Menu {
            id: viewMenu
            title: qsTr("View")
            MenuItem {
                id: noFocusModeItem
                text: qsTr("Game Focus Mode")
                checkable: true
                checked: controller ? controller.noFocusMode : false
                enabled: controller ? controller.isNoFocusModeAvailable() : false
                onTriggered: { 
                    viewMenu.close()
                    Qt.callLater(function(){ 
                        if (controller) controller.noFocusMode = checked
                    })
                }
            }
            MenuItem {
                id: mouseIsolationItem
                text: qsTr("Isolate Mouse (grab physical mouse)")
                checkable: true
                checked: controller ? controller.mouseIsolationActive : false
                enabled: controller ? controller.isMouseIsolationAvailable() : false
                onTriggered: {
                    viewMenu.close()
                    Qt.callLater(function(){
                        if (!controller) return
                        if (mouseIsolationItem.checked) {
                            if (!controller.startMouseIsolation()) mouseIsolationItem.checked = false
                        } else {
                            controller.stopMouseIsolation()
                        }
                    })
                }
            }
            MenuItem {
                text: qsTr("Borderless Gaming...")
                enabled: controller ? controller.isBorderlessAvailable() : false
                onTriggered: {
                    viewMenu.close()
                    Qt.callLater(function(){ borderlessGamingDialog.open(); })
                }
            }
            MenuSeparator {}
            MenuItem {
                id: controllerMonitorItem
                text: qsTr("Controller Monitor")
                checkable: true
                checked: root.controllerMonitorVisible
                onTriggered: { viewMenu.close(); Qt.callLater(function(){ root.controllerMonitorVisible = checked; }); }
            }
            MenuItem {
                id: debugBordersItem
                text: qsTr("Debug Borders")
                checkable: true
                checked: controller ? controller.debugBorders : false
                onTriggered: { viewMenu.close(); Qt.callLater(function(){ if (controller) controller.debugBorders = checked; }); }
            }
        }
        
        Menu {
            id: helpMenu
            title: qsTr("Help")
            MenuItem {
                text: qsTr("Getting Started...")
                onTriggered: {
                    helpMenu.close()
                    Qt.callLater(function(){ gettingStartedDialog.open(); })
                }
            }
            MenuItem {
                text: qsTr("Feature Guide...")
                onTriggered: {
                    helpMenu.close()
                    Qt.callLater(function(){ featureGuideDialog.open(); })
                }
            }
            MenuSeparator {}
            MenuItem { 
                text: qsTr("GitHub Repository")
                onTriggered: { 
                    helpMenu.close()
                    Qt.openUrlExternally("https://github.com/owenpkent/Project-Nimbus")
                } 
            }
            MenuSeparator {}
            MenuItem { 
                text: qsTr("About Nimbus Adaptive Controller...")
                onTriggered: { 
                    helpMenu.close()
                    Qt.callLater(function(){ aboutDialog.open(); })
                } 
            }
        }
    }
    
    // About Dialog
    Dialog {
        id: aboutDialog
        title: "About Nimbus Adaptive Controller"
        modal: true
        anchors.centerIn: parent
        width: 450
        height: 320
        
        background: Rectangle {
            color: "#2a2a2a"
            border.color: "#555"
            border.width: 1
            radius: 8
        }
        
        header: Label {
            text: aboutDialog.title
            color: "white"
            font.pixelSize: 16
            font.bold: true
            padding: 16
            background: Rectangle { color: "#333"; radius: 8 }
        }
        
        contentItem: Column {
            spacing: 16
            padding: 20
            
            Image {
                source: "qrc:/logo.png"
                width: 80
                height: 80
                anchors.horizontalCenter: parent.horizontalCenter
                fillMode: Image.PreserveAspectFit
                visible: status === Image.Ready
            }
            
            Label {
                text: "Nimbus Adaptive Controller"
                color: "white"
                font.pixelSize: 20
                font.bold: true
                anchors.horizontalCenter: parent.horizontalCenter
            }
            
            Label {
                text: "Version " + (controller ? controller.getVersion() : "1.4.0")
                color: "#aaa"
                font.pixelSize: 14
                anchors.horizontalCenter: parent.horizontalCenter
            }
            
            Label {
                text: "Virtual controller interface for accessibility and adaptive gaming.\nTransforms mouse input into virtual joystick commands."
                color: "#ccc"
                font.pixelSize: 12
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                width: parent.width - 40
                anchors.horizontalCenter: parent.horizontalCenter
            }
            
            Label {
                text: "© 2024-2025 Owen Kent"
                color: "#888"
                font.pixelSize: 11
                anchors.horizontalCenter: parent.horizontalCenter
            }
            
            Label {
                text: "Licensed under MIT License"
                color: "#666"
                font.pixelSize: 10
                anchors.horizontalCenter: parent.horizontalCenter
            }
        }
        
        footer: DialogButtonBox {
            Button {
                text: "OK"
                DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
            }
            background: Rectangle { color: "#333" }
        }
    }

    // Getting Started Dialog
    Dialog {
        id: gettingStartedDialog
        title: "Getting Started"
        modal: true
        anchors.centerIn: parent
        width: 560
        height: 500

        background: Rectangle { color: "#2a2a2a"; border.color: "#555"; border.width: 1; radius: 8 }
        header: Label { text: gettingStartedDialog.title; color: "white"; font.pixelSize: 16; font.bold: true; padding: 16; background: Rectangle { color: "#333"; radius: 8 } }

        contentItem: Flickable {
            contentHeight: gsCol.implicitHeight + 20
            clip: true
            flickableDirection: Flickable.VerticalFlick
            Basic.ScrollBar.vertical: Basic.ScrollBar { policy: Basic.ScrollBar.AsNeeded }

            Column {
                id: gsCol
                width: parent.width - 20
                x: 10; y: 10
                spacing: 14

                Label { text: "Welcome to Nimbus Adaptive Controller"; color: "#4a9eff"; font.pixelSize: 15; font.bold: true; wrapMode: Text.WordWrap; width: parent.width }
                Label { text: "Nimbus Adaptive Controller transforms mouse, wheelchair joystick, or other pointing device input into virtual game controller commands via vJoy."; color: "#ccc"; font.pixelSize: 12; wrapMode: Text.WordWrap; width: parent.width }

                Label { text: "1. Choose a Profile"; color: "#ff8833"; font.pixelSize: 13; font.bold: true }
                Label { text: "Go to File → Profile to select a layout:\n• Flight Simulator — dual joysticks, throttle, rudder\n• Xbox Controller — gamepad-style layout\n• Adaptive Platform — accessibility-focused with large buttons\n• Custom layouts — fully configurable drag-and-drop canvas"; color: "#ccc"; font.pixelSize: 12; wrapMode: Text.WordWrap; width: parent.width }

                Label { text: "2. Custom Layout (Recommended)"; color: "#ff8833"; font.pixelSize: 13; font.bold: true }
                Label { text: "Click Edit Layout in the menu bar to enter edit mode. The Widget Palette appears with:\n• Joystick — 2-axis stick (up to 4 pairs)\n• Button — up to 128 buttons with toggle/momentary modes\n• H/V Slider — single-axis controls (throttle, triggers)\n• D-Pad — 4-directional digital pad\n• Wheel — rotational single-axis\n\nDrag widgets onto the canvas, resize them, and double-click to configure. Click Done Editing when finished."; color: "#ccc"; font.pixelSize: 12; wrapMode: Text.WordWrap; width: parent.width }

                Label { text: "3. Wheelchair Joystick Setup"; color: "#ff8833"; font.pixelSize: 13; font.bold: true }
                Label { text: "For wheelchair-mounted joysticks that control the mouse cursor:\n\n1. Add a Joystick widget and double-click to configure\n2. Enable Triple-click lock (on by default)\n3. Enable Auto-return to center when locked\n4. Set Delay to 1-5ms for responsive return\n5. Adjust Lock Sensitivity (higher = more responsive)\n6. Set Tremor Filter if your input is jittery (higher = smoother)\n\nTriple-click the joystick to lock. Your cursor becomes invisible and is anchored to the joystick center. Physical joystick movement translates directly to virtual deflection. Triple-click again to unlock."; color: "#ccc"; font.pixelSize: 12; wrapMode: Text.WordWrap; width: parent.width }

                Label { text: "4. Per-Widget Settings"; color: "#ff8833"; font.pixelSize: 13; font.bold: true }
                Label { text: "Double-click any widget to configure:\n• Axis mapping — which vJoy axis to control\n• Sensitivity — response curve (0-100%)\n• Dead zone — ignore small inputs near center\n• Extremity dead zone — snap to full at edges\n• Copy settings from other widgets via dropdown"; color: "#ccc"; font.pixelSize: 12; wrapMode: Text.WordWrap; width: parent.width }

                Label { text: "5. Game Focus Mode"; color: "#ff8833"; font.pixelSize: 13; font.bold: true }
                Label { text: "Enable via View → Game Focus Mode. When active, clicking Nimbus Adaptive Controller won't steal focus from your game — the game window stays in the foreground."; color: "#ccc"; font.pixelSize: 12; wrapMode: Text.WordWrap; width: parent.width }

                Label { text: "6. Borderless Gaming"; color: "#ff8833"; font.pixelSize: 13; font.bold: true }
                Label { text: "Go to View → Borderless Gaming to free your cursor from games that lock it.\n\n• Auto-detects running games from our compatibility database\n• Converts windowed games to borderless fullscreen\n• Continuously releases cursor lock so you can reach Nimbus\n• Works with most indie, strategy, and older games\n• See the Compatibility tab for a full list of tested games"; color: "#ccc"; font.pixelSize: 12; wrapMode: Text.WordWrap; width: parent.width }
            }
        }

        footer: DialogButtonBox {
            Button {
                text: "Close"
                DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
            }
            background: Rectangle { color: "#333" }
        }
    }

    // Feature Guide Dialog
    Dialog {
        id: featureGuideDialog
        title: "Feature Guide"
        modal: true
        anchors.centerIn: parent
        width: 560
        height: 500

        background: Rectangle { color: "#2a2a2a"; border.color: "#555"; border.width: 1; radius: 8 }
        header: Label { text: featureGuideDialog.title; color: "white"; font.pixelSize: 16; font.bold: true; padding: 16; background: Rectangle { color: "#333"; radius: 8 } }

        contentItem: Flickable {
            contentHeight: fgCol.implicitHeight + 20
            clip: true
            flickableDirection: Flickable.VerticalFlick
            Basic.ScrollBar.vertical: Basic.ScrollBar { policy: Basic.ScrollBar.AsNeeded }

            Column {
                id: fgCol
                width: parent.width - 20
                x: 10; y: 10
                spacing: 14

                Label { text: "Widget Types"; color: "#4a9eff"; font.pixelSize: 15; font.bold: true; wrapMode: Text.WordWrap; width: parent.width }
                Label { text: "• Joystick — 2-axis control (X/Y, RX/RY, Z/RZ, SL0/SL1). Supports triple-click lock for wheelchair joysticks with auto-return, sensitivity, and tremor filtering.\n• Button — Momentary (hold) or Toggle (click on/off). Up to 128 buttons, customizable color and shape.\n• H Slider / V Slider — Single-axis with snap modes: Hold position, Return to zero, or Spring to center.\n• D-Pad — 4 directional buttons mapped to vJoy button IDs.\n• Steering Wheel — Rotational single-axis control."; color: "#ccc"; font.pixelSize: 12; wrapMode: Text.WordWrap; width: parent.width }

                Label { text: "Joystick Lock Mode (FPS-Style Delta Tracking)"; color: "#4a9eff"; font.pixelSize: 15; font.bold: true; wrapMode: Text.WordWrap; width: parent.width }
                Label { text: "When you triple-click a joystick:\n• Cursor becomes invisible and is anchored to joystick center\n• Mouse movements are treated as deflection deltas\n• Cursor is continuously warped back to center (like FPS mouse look)\n• Auto-return snaps joystick to center when you stop moving\n\nThis is designed for wheelchair joysticks where the physical stick controls cursor velocity. The virtual joystick deflection directly mirrors the physical stick position."; color: "#ccc"; font.pixelSize: 12; wrapMode: Text.WordWrap; width: parent.width }

                Label { text: "Lock Settings"; color: "#4a9eff"; font.pixelSize: 15; font.bold: true }
                Label { text: "• Lock Sensitivity (1-10) — How responsive the locked joystick is. Higher values require less physical movement for full deflection. Start at 4 and adjust.\n• Tremor Filter (0-10) — Smooths out jittery input. 0 = off, higher = more smoothing. Useful for wheelchair joysticks with tremor.\n• Auto-return Delay (1-10ms) — How quickly the joystick returns to center when you stop moving. Lower = faster snap back."; color: "#ccc"; font.pixelSize: 12; wrapMode: Text.WordWrap; width: parent.width }

                Label { text: "Sensitivity & Dead Zones"; color: "#4a9eff"; font.pixelSize: 15; font.bold: true }
                Label { text: "Each axis widget has per-widget settings:\n• Sensitivity (0-100%) — Response curve. 50% = linear, lower = more precision near center, higher = more responsive.\n• Dead Zone (0-100%) — Ignores small inputs near the center position.\n• Extremity Dead Zone (0-100%) — Snaps to full deflection near the edges.\n• Copy From — Copy settings from another axis widget."; color: "#ccc"; font.pixelSize: 12; wrapMode: Text.WordWrap; width: parent.width }

                Label { text: "Virtual Controller Output"; color: "#4a9eff"; font.pixelSize: 15; font.bold: true }
                Label { text: "Project Nimbus outputs to:\n• vJoy — 8 axes (4 joystick pairs max), 128 buttons. Must have vJoy driver installed.\n• ViGEm (planned) — Xbox 360 controller emulation with 2 sticks, 2 triggers, 14 buttons."; color: "#ccc"; font.pixelSize: 12; wrapMode: Text.WordWrap; width: parent.width }

                Label { text: "Keyboard Shortcuts"; color: "#4a9eff"; font.pixelSize: 15; font.bold: true }
                Label { text: "• Triple-click — Lock/unlock joystick\n• Double-click widget (edit mode) — Open widget config\n• Drag widget edges (edit mode) — Resize"; color: "#ccc"; font.pixelSize: 12; wrapMode: Text.WordWrap; width: parent.width }
            }
        }

        footer: DialogButtonBox {
            Button {
                text: "Close"
                DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
            }
            background: Rectangle { color: "#333" }
        }
    }

    // Borderless Gaming Dialog
    Comp.BorderlessGamingDialog {
        id: borderlessGamingDialog
    }

    // ===== GAME MODE BUTTON + WINDOW PICKER =====

    // Filtered window list for picker
    property var _gameModeWindowList: []
    property var _gameModeDiag: ({})
    property string _gameModeStatus: ""

    function _refreshGameWindows() {
        // Also fetch diagnostics
        if (controller) root._gameModeDiag = controller.getGameModeDiagnostics() || {}
        var skipTitles = [
            "nimbus", "edge", "chrome", "firefox", "opera", "brave",
            "internet explorer", "microsoft store", "windows security",
            "task manager", "program manager", "windows shell experience",
            "search", "cortana", "start", "action center", "settings",
            "explorer", "file explorer", "notepad", "visual studio code",
            "windsurf", "discord", "slack", "teams", "zoom", "obs ",
            "steam", "epic games launcher", "gog galaxy"
        ]
        var skipClasses = [
            "shell_traywnd", "progman", "button", "tooltips_class32",
            "workerw", "dv2controlhost", "windows.ui.core.corewindow"
        ]
        var all = controller ? controller.getWindowList() : []
        var filtered = []
        for (var i = 0; i < all.length; i++) {
            var w = all[i]
            var t = (w.title || "").toLowerCase()
            var c = (w.className || "").toLowerCase()
            var skip = false
            for (var j = 0; j < skipTitles.length; j++) {
                if (t.indexOf(skipTitles[j]) >= 0) { skip = true; break }
            }
            for (var k = 0; k < skipClasses.length; k++) {
                if (c.indexOf(skipClasses[k]) >= 0) { skip = true; break }
            }
            // Must be reasonably large (games are usually > 400px)
            if (w.width < 400 || w.height < 300) skip = true
            if (!skip) filtered.push(w)
        }
        root._gameModeWindowList = filtered
    }

    // Window picker popup
    Rectangle {
        id: gamePickerPopup
        visible: false
        anchors.bottom: parent.bottom
        anchors.right: parent.right
        anchors.bottomMargin: 6
        anchors.rightMargin: 4
        width: 340
        height: Math.min(pickerCol.implicitHeight + 16, 320)
        radius: 8
        color: "#1e1e1e"
        border.color: "#4a9eff"
        border.width: 1
        z: 600
        clip: true

        Flickable {
            anchors.fill: parent
            anchors.margins: 8
            contentHeight: pickerCol.implicitHeight
            clip: true
            flickableDirection: Flickable.VerticalFlick

            Column {
                id: pickerCol
                width: parent.width
                spacing: 4

                Label {
                    text: "Select your game:"
                    color: "#4a9eff"
                    font.pixelSize: 12
                    font.bold: true
                    bottomPadding: 4
                }

                // ViGEm status warning
                Rectangle {
                    visible: root._gameModeDiag.vigem_package !== true || root._gameModeDiag.driver_installed !== true
                    width: pickerCol.width
                    height: vigemWarnCol.implicitHeight + 10
                    radius: 4
                    color: "#3a2020"
                    border.color: "#aa4444"
                    border.width: 1

                    Column {
                        id: vigemWarnCol
                        anchors.fill: parent
                        anchors.margins: 5
                        spacing: 2
                        Label {
                            text: "ViGEmBus driver not detected"
                            color: "#ff6666"
                            font.pixelSize: 11
                            font.bold: true
                        }
                        Label {
                            text: "Controller mode requires ViGEmBus.\nInstall: pip install vgamepad\n(accepts driver prompt, then restart)"
                            color: "#cc8888"
                            font.pixelSize: 10
                            wrapMode: Text.WordWrap
                            width: pickerCol.width - 10
                        }
                    }
                }

                // Status after starting
                Label {
                    visible: root._gameModeStatus !== ""
                    text: root._gameModeStatus
                    color: root._gameModeStatus.indexOf("FAIL") >= 0 ? "#ff4444" : "#00cc44"
                    font.pixelSize: 11
                    font.bold: true
                    wrapMode: Text.WordWrap
                    width: pickerCol.width
                    bottomPadding: 4
                }

                Repeater {
                    model: root._gameModeWindowList
                    delegate: Rectangle {
                        width: pickerCol.width
                        height: 36
                        radius: 4
                        color: pickerItemMa.containsMouse ? "#2a4a7a" : "#252525"
                        border.color: pickerItemMa.containsMouse ? "#4a9eff" : "#333"
                        border.width: 1

                        Row {
                            anchors.fill: parent
                            anchors.leftMargin: 8
                            anchors.rightMargin: 8
                            spacing: 8

                            Label {
                                anchors.verticalCenter: parent.verticalCenter
                                text: modelData.title || "(untitled)"
                                color: "white"
                                font.pixelSize: 12
                                elide: Text.ElideRight
                                width: parent.width - sizeLabel.width - 8
                            }
                            Label {
                                id: sizeLabel
                                anchors.verticalCenter: parent.verticalCenter
                                text: modelData.width + "×" + modelData.height
                                color: "#666"
                                font.pixelSize: 10
                            }
                        }

                        MouseArea {
                            id: pickerItemMa
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                gamePickerPopup.visible = false
                                var hwnd = modelData.hwnd
                                var title = modelData.title || ""
                                if (controller && hwnd > 0) {
                                    var ok = controller.startFullGameMode(hwnd, 30)
                                    if (ok) {
                                        root.gameModeHwnd = hwnd
                                        root.gameModeTitle = title
                                        root.gameModeActive = true
                                    }
                                }
                            }
                        }
                    }
                }

                // No windows found message
                Label {
                    visible: root._gameModeWindowList.length === 0
                    text: "No game windows found.\nMake sure your game is running."
                    color: "#888"
                    font.pixelSize: 11
                    wrapMode: Text.WordWrap
                    width: pickerCol.width
                    topPadding: 8
                    bottomPadding: 8
                }
            }
        }
    }

    // Click outside picker to close it
    MouseArea {
        anchors.fill: parent
        z: 590
        visible: gamePickerPopup.visible
        onClicked: gamePickerPopup.visible = false
    }

    // ==================== STATUS RIBBON (VS Code-style) ====================
    footer: Rectangle {
        id: statusRibbon
        width: parent.width
        height: 22
        color: "#0d1117"

        // Top border line
        Rectangle {
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            height: 1
            color: "#2a3a4a"
        }

        // --- Left side: connection dot + controller type (clickable to change output mode) ---
        Item {
            id: ribbonLeftSection
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.leftMargin: 8
            width: ribbonLeftRow.implicitWidth + 20

            Row {
                id: ribbonLeftRow
                anchors.verticalCenter: parent.verticalCenter
                anchors.left: parent.left
                spacing: 6

                Rectangle {
                    id: ribbonDot
                    width: 7; height: 7; radius: 3.5
                    anchors.verticalCenter: parent.verticalCenter
                    color: "#666"
                }

                Text {
                    id: ribbonStatusText
                    anchors.verticalCenter: parent.verticalCenter
                    text: controller ? controller.getControllerType() : "No controller"
                    color: outputModeMa.containsMouse ? "#ccc" : "#888"
                    font.pixelSize: 10
                    font.family: "Segoe UI, sans-serif"
                }

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "▲"
                    color: outputModeMa.containsMouse ? "#ccc" : "#555"
                    font.pixelSize: 7
                }
            }

            MouseArea {
                id: outputModeMa
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: outputModeMenu.popup()
            }

            Menu {
                id: outputModeMenu

                MenuItem {
                    text: controller ? controller.getOutputModeLabel("vjoy") : "vJoy (DirectInput)"
                    checkable: true
                    checked: root.outputMode === "vjoy"
                    onTriggered: {
                        if (controller) controller.setOutputMode("vjoy")
                    }
                }
                MenuItem {
                    text: controller ? controller.getOutputModeLabel("vigem") : "Xbox 360 (ViGEm)"
                    checkable: true
                    checked: root.outputMode === "vigem"
                    enabled: controller ? controller.isVigemAvailable() : false
                    onTriggered: {
                        if (controller) controller.setOutputMode("vigem")
                    }
                }
            }
        }

        // Polling timer — updates dot colour and status / monitor text
        Timer {
            interval: 200
            running: true
            repeat: true
            onTriggered: {
                if (!controller) return
                ribbonDot.color = controller.isVJoyConnected() ? "#4caf50" : "#f44336"
                ribbonStatusText.text = root.controllerMonitorVisible
                    ? controller.getControllerStateText()
                    : controller.getControllerType()
            }
        }

        // --- Right side: Edit Layout + Game Mode buttons ---
        Row {
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            spacing: 0

            // Edit Layout toggle (custom layout only)
            Rectangle {
                visible: root.layoutType === "custom"
                width: visible ? editRibbonLabel.implicitWidth + 16 : 0
                height: parent.height
                color: editRibbonMa.containsMouse
                    ? "#1e2e40"
                    : (layoutLoader.item && layoutLoader.item.editMode ? "#12283a" : "transparent")

                Text {
                    id: editRibbonLabel
                    anchors.centerIn: parent
                    text: layoutLoader.item && layoutLoader.item.editMode ? "✓ Done Editing" : "✏ Edit Layout"
                    color: layoutLoader.item && layoutLoader.item.editMode ? "#4a9eff" : "#aaa"
                    font.pixelSize: 10
                }

                MouseArea {
                    id: editRibbonMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        if (layoutLoader.item) {
                            layoutLoader.item.editMode = !layoutLoader.item.editMode
                            if (!layoutLoader.item.editMode) layoutLoader.item._saveLayout()
                        }
                    }
                }
            }

            // Divider
            Rectangle {
                width: 1; height: 12
                color: "#333"
                anchors.verticalCenter: parent.verticalCenter
                visible: root.layoutType === "custom"
            }

            // Game Mode button
            Rectangle {
                id: gameModeRibbonBtn
                width: gameModeRibbonRow.implicitWidth + 16
                height: parent.height
                color: gameModeRibbonMa.containsMouse
                    ? (root.gameModeActive ? "#0a2a18" : "#1e2020")
                    : (root.gameModeActive ? "#071810" : "transparent")

                Row {
                    id: gameModeRibbonRow
                    anchors.centerIn: parent
                    spacing: 5

                    Rectangle {
                        id: gameModeDot
                        width: 6; height: 6; radius: 3
                        anchors.verticalCenter: parent.verticalCenter
                        color: root.gameModeActive ? "#00cc44" : "#666"

                        SequentialAnimation on opacity {
                            running: root.gameModeActive
                            loops: Animation.Infinite
                            NumberAnimation { to: 0.3; duration: 600 }
                            NumberAnimation { to: 1.0; duration: 600 }
                        }
                    }

                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: root.gameModeActive
                            ? ("■ " + (root.gameModeTitle !== "" ? root.gameModeTitle : "Game Mode"))
                            : "▶ Game Mode"
                        color: root.gameModeActive ? "#00cc44" : "#aaa"
                        font.pixelSize: 10
                    }
                }

                MouseArea {
                    id: gameModeRibbonMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        if (!controller) return
                        if (root.gameModeActive) {
                            controller.stopFullGameMode(root.gameModeHwnd)
                            root.gameModeActive = false
                            root.gameModeHwnd = 0
                            root.gameModeTitle = ""
                            gamePickerPopup.visible = false
                        } else if (!controller.isBorderlessAvailable()) {
                            // No window picker on this platform (Linux):
                            // Game Mode = no-focus window + controller keep-alive pulse.
                            var okNoPicker = controller.startFullGameMode(0, 30)
                            if (okNoPicker) {
                                root.gameModeHwnd = 0
                                root.gameModeTitle = "Controller Mode"
                                root.gameModeActive = true
                            }
                        } else {
                            root._refreshGameWindows()
                            gamePickerPopup.visible = !gamePickerPopup.visible
                        }
                    }
                }
            }

            // Right padding
            Item { width: 4; height: parent.height }
        }
    }

    // Software cursor shown while the physical mouse is grabbed (Mouse Isolation, Linux).
    // The bridge delivers synthetic mouse events in window coordinates at this position;
    // this item only draws. It lives in the window overlay so it uses window coordinates
    // (the content item starts below the menu bar) and stays above open menus.
    Shape {
        id: isolationCursor
        objectName: "isolationCursor"
        parent: Overlay.overlay
        visible: controller ? controller.mouseIsolationActive : false
        x: controller ? controller.isolationCursorX : 0
        y: controller ? controller.isolationCursorY : 0
        width: 20
        height: 26
        z: 10000
        enabled: false
        antialiasing: true
        ShapePath {
            strokeColor: "black"
            strokeWidth: 1.5
            fillColor: "white"
            startX: 1; startY: 1
            PathLine { x: 1; y: 20 }
            PathLine { x: 6; y: 15.5 }
            PathLine { x: 9.5; y: 23 }
            PathLine { x: 12.5; y: 21.7 }
            PathLine { x: 9; y: 14.2 }
            PathLine { x: 16; y: 14.2 }
            PathLine { x: 1; y: 1 }
        }
    }

    // Layout loader - switches between Flight Sim, Xbox, Adaptive, and Custom layouts
    Loader {
        id: layoutLoader
        anchors.fill: parent
        sourceComponent: {
            if (root.layoutType === "custom") return customLayout
            if (root.layoutType === "adaptive") return adaptiveLayout
            if (root.layoutType === "xbox") return xboxLayout
            return flightSimLayout
        }
    }

    // Flight Simulator layout component
    Component {
        id: flightSimLayout
        Layouts.FlightSimLayout {
            scaleFactor: root.scaleFactor
        }
    }

    // Xbox Controller layout component
    Component {
        id: xboxLayout
        Layouts.XboxLayout {
            scaleFactor: root.scaleFactor
        }
    }

    // Adaptive Controller layout component (accessibility-focused)
    Component {
        id: adaptiveLayout
        Layouts.AdaptiveLayout {
            scaleFactor: root.scaleFactor
        }
    }

    // Custom modular layout component (drag-and-drop canvas)
    Component {
        id: customLayout
        Layouts.CustomLayout {
            scaleFactor: root.scaleFactor
            outputMode: root.outputMode
            mainWindow: root
        }
    }
}
