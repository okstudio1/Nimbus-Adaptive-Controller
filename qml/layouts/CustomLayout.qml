import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Controls.Basic 2.15 as Basic
import QtQuick.Layouts 1.15
import QtQuick.Window 2.15
import "../components" as Comp

Item {
    id: root
    property real scaleFactor: 1.0
    property string outputMode: "vjoy"
    property var mainWindow: null

    // Edit mode state
    property bool editMode: false
    property bool showGrid: true
    property int gridSnap: 10

    // Joystick mouse-lock tracking
    property string lockedJoystickId: ""
    property var lockedWidget: null

    // Widget model - loaded from profile via bridge
    property var widgetModel: []

    // Load layout from bridge on creation
    Component.onCompleted: {
        if (controller) {
            var data = controller.getCustomLayout()
            if (data && data.length > 0) {
                widgetModel = JSON.parse(data)
                gridSnap = controller.getCustomLayoutGridSnap()
                showGrid = controller.getCustomLayoutShowGrid()
            }
        }
    }

    // Reload when the active profile changes
    Connections {
        target: controller
        function onProfileChanged(profileId) {
            var data = controller.getCustomLayout()
            root.widgetModel = (data && data.length > 0) ? JSON.parse(data) : []
            root.gridSnap = controller.getCustomLayoutGridSnap()
            root.showGrid = controller.getCustomLayoutShowGrid()
            gridCanvas.requestPaint()
        }
    }

    // ==================== CANVAS BACKGROUND ====================
    Rectangle {
        id: canvas
        anchors.fill: parent
        color: "#111111"
        clip: true
        // Stable reference to outer layout (avoids shadowing by DraggableWidget's own id:root)
        readonly property var layout: root

        // Grid overlay
        Canvas {
            id: gridCanvas
            anchors.fill: parent
            visible: editMode && showGrid
            onPaint: {
                var ctx = getContext("2d")
                ctx.clearRect(0, 0, width, height)
                ctx.strokeStyle = "#222222"
                ctx.lineWidth = 0.5

                var step = root.gridSnap > 0 ? root.gridSnap : 10

                // Vertical lines
                for (var x = 0; x <= width; x += step) {
                    ctx.beginPath()
                    ctx.moveTo(x, 0)
                    ctx.lineTo(x, height)
                    ctx.stroke()
                }
                // Horizontal lines
                for (var y = 0; y <= height; y += step) {
                    ctx.beginPath()
                    ctx.moveTo(0, y)
                    ctx.lineTo(width, y)
                    ctx.stroke()
                }
            }
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            onVisibleChanged: requestPaint()
        }

        // Edit mode instruction banner
        Rectangle {
            anchors.top: parent.top
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.topMargin: 8
            width: editBannerText.width + 30
            height: 28
            radius: 14
            color: "#333"
            border.color: "#4a9eff"
            border.width: 1
            visible: editMode
            z: 200

            Text {
                id: editBannerText
                anchors.centerIn: parent
                text: "Edit Mode — Drag to move, corner to resize, double-click to configure"
                color: "#4a9eff"
                font.pixelSize: 11
                font.bold: true
            }
        }

        // ==================== WIDGET REPEATER ====================
        Repeater {
            id: widgetRepeater
            model: root.widgetModel

            Comp.DraggableWidget {
                id: dragWidget

                // Pull data from model
                property var wData: modelData || {}
                // Lets tests and tooling find a widget by its profile id
                objectName: "widget_" + (wData.id || "")

                x: wData.x || 0
                y: wData.y || 0
                width: wData.width || 100
                height: wData.height || 100

                widgetId: wData.id || ""
                widgetType: wData.type || "button"
                widgetLabel: wData.label || ""
                buttonId: wData.button_id || 1
                widgetColor: wData.color || "#333"
                widgetShape: wData.shape || "rounded"
                orientation: wData.orientation || "horizontal"
                mapping: wData.mapping || {}
                snapMode: wData.snap_mode || "none"
                clickMode: wData.click_mode || "jump"
                toggleMode: wData.toggle_mode || false
                tripleClickEnabled: wData.triple_click_enabled !== undefined ? wData.triple_click_enabled : true
                autoCenter: wData.auto_center || false
                autoCenterDelay: wData.auto_center_delay !== undefined ? Math.max(1, Math.min(10, wData.auto_center_delay)) : 5
                lockSensitivity: wData.lock_sensitivity !== undefined ? wData.lock_sensitivity : 4.0
                tremorFilter: wData.tremor_filter !== undefined ? wData.tremor_filter : 0.0
                sensitivity: wData.sensitivity !== undefined ? wData.sensitivity : 50.0
                deadZone: wData.dead_zone !== undefined ? wData.dead_zone : 0.0
                extremityDeadZone: wData.extremity_dead_zone !== undefined ? wData.extremity_dead_zone : 5.0
                invert_x: wData.invert_x || false
                invert_y: wData.invert_y || false
                macroMode: wData.macro_mode || false
                macroConfig: wData.macro_config || { "zones": {}, "deadzone_percent": 30, "diagonal_mode": "8-way" }

                editMode: root.editMode
                gridSnap: root.gridSnap

                onWidgetMoved: function(wid, wx, wy) {
                    canvas.layout._updateWidgetProp(wid, "x", wx)
                    canvas.layout._updateWidgetProp(wid, "y", wy)
                    canvas.layout._autoSave()
                }

                onWidgetResized: function(wid, ww, wh) {
                    canvas.layout._updateWidgetProp(wid, "width", ww)
                    canvas.layout._updateWidgetProp(wid, "height", wh)
                    canvas.layout._autoSave()
                }

                onWidgetRemoved: function(wid) {
                    canvas.layout._removeWidget(wid)
                    canvas.layout._autoSave()
                }

                onWidgetConfigRequested: function(wid) {
                    configDialog.openForWidget(wid)
                }

                onMouseLockChanged: function(wid, locked) {
                    if (locked) {
                        canvas.layout.lockedJoystickId = wid
                        canvas.layout.lockedWidget = dragWidget
                        joyLockOverlay._warping = true
                        warpSafetyTimer.restart()
                        if (controller) {
                            controller.clipCursorToWindow()
                            var center = dragWidget.mapToGlobal(dragWidget.width / 2, dragWidget.height / 2)
                            controller.setCursorPos(Math.round(center.x), Math.round(center.y))
                        }
                    } else {
                        canvas.layout.lockedJoystickId = ""
                        canvas.layout.lockedWidget = null
                        if (controller) controller.unclipCursor()
                    }
                }
            }
        }
    }

    // ==================== MOUSE LOCK OVERLAY ====================
    // Full-canvas hover overlay that tracks mouse when a joystick is locked
    MouseArea {
        id: joyLockOverlay
        anchors.fill: parent
        visible: root.lockedJoystickId !== "" && !root.editMode
        hoverEnabled: true
        cursorShape: Qt.BlankCursor
        z: 200

        // Track current joystick position for auto-center easing
        property real lockNx: 0
        property real lockNy: 0
        property bool _animating: false
        property bool _warping: false   // true while SetCursorPos is in flight; skip stale events

        // EMA tremor filter state
        property real _smoothNx: 0
        property real _smoothNy: 0

        // Safety: clear _warping after a short delay in case SetCursorPos
        // doesn't generate a QML positionChanged event
        Timer {
            id: warpSafetyTimer
            interval: 100
            repeat: false
            onTriggered: {
                if (joyLockOverlay._warping) {
                    joyLockOverlay._warping = false
                    joyLockOverlay.lockNx = 0
                    joyLockOverlay.lockNy = 0
                    if (root.lockedWidget)
                        root.lockedWidget.updateJoystickPosition(0, 0)
                }
            }
        }

        onLockNxChanged: {
            if (_animating && root.lockedWidget)
                root.lockedWidget.updateJoystickPosition(lockNx, lockNy)
        }
        onLockNyChanged: {
            if (_animating && root.lockedWidget)
                root.lockedWidget.updateJoystickPosition(lockNx, lockNy)
        }

        // Auto-center: fires when mouse stops moving
        Timer {
            id: autoCenterTimer
            interval: root.lockedWidget ? root.lockedWidget.autoCenterDelay : 200
            repeat: false
            onTriggered: {
                if (root.lockedWidget && root.lockedWidget.autoCenter) {
                    var delay = root.lockedWidget.autoCenterDelay
                    if (delay <= 0) {
                        // Instant snap — no animation
                        joyLockOverlay.lockNx = 0
                        joyLockOverlay.lockNy = 0
                        root.lockedWidget.updateJoystickPosition(0, 0)
                    } else {
                        // Eased return — animation duration scales with delay (min 100ms)
                        var animDur = Math.max(100, Math.min(delay, 300))
                        autoCenterAnimX.duration = animDur
                        autoCenterAnimY.duration = animDur
                        joyLockOverlay._animating = true
                        autoCenterAnimX.to = 0
                        autoCenterAnimY.to = 0
                        autoCenterAnim.start()
                    }
                }
            }
        }

        ParallelAnimation {
            id: autoCenterAnim
            NumberAnimation { id: autoCenterAnimX; target: joyLockOverlay; property: "lockNx"; duration: 300; easing.type: Easing.OutCubic }
            NumberAnimation { id: autoCenterAnimY; target: joyLockOverlay; property: "lockNy"; duration: 300; easing.type: Easing.OutCubic }
            onStopped: joyLockOverlay._animating = false
        }

        onPositionChanged: function(mouse) {
            if (!root.lockedWidget) return

            var localPt = mapToItem(root.lockedWidget, mouse.x, mouse.y)
            var dx = localPt.x - root.lockedWidget.width / 2
            var dy = localPt.y - root.lockedWidget.height / 2

            // Skip warp-back events (cursor near center = our SetCursorPos just landed)
            // Also skips stale initial event from lock
            if (Math.abs(dx) < 3 && Math.abs(dy) < 3) {
                _warping = false
                return
            }

            // Skip stale events before initial warp lands
            if (_warping) return

            // Stop any in-progress auto-center animation
            autoCenterAnim.stop()
            _animating = false

            // Scale delta to -1..1: multiplier controls how far cursor must
            // move from center for full deflection. Configurable per-widget.
            var scale = root.lockedWidget.lockSensitivity * 2
            var half_w = root.lockedWidget.width / 2
            var half_h = root.lockedWidget.height / 2
            var rawNx = Math.max(-1, Math.min(1, (dx / half_w) * scale))
            var rawNy = Math.max(-1, Math.min(1, (dy / half_h) * scale))

            // Apply EMA tremor filter: higher tremorFilter value = more smoothing
            var tf = root.lockedWidget.tremorFilter
            if (tf > 0) {
                // alpha: 1.0 (no smooth) to 0.1 (heavy smooth)
                var alpha = 1.0 - (tf / 10.0) * 0.9
                _smoothNx = _smoothNx * (1.0 - alpha) + rawNx * alpha
                _smoothNy = _smoothNy * (1.0 - alpha) + rawNy * alpha
                lockNx = _smoothNx
                lockNy = _smoothNy
            } else {
                lockNx = rawNx
                lockNy = rawNy
                _smoothNx = rawNx
                _smoothNy = rawNy
            }
            root.lockedWidget.updateJoystickPosition(lockNx, lockNy)

            // Warp cursor back to joystick center (FPS-style)
            if (controller) {
                var center = root.lockedWidget.mapToGlobal(
                    root.lockedWidget.width / 2,
                    root.lockedWidget.height / 2
                )
                controller.setCursorPos(Math.round(center.x), Math.round(center.y))
            }

            // Restart auto-center timer if enabled
            if (root.lockedWidget.autoCenter) autoCenterTimer.restart()
        }

        onClicked: function(mouse) {
            if (root.lockedWidget) {
                root.lockedWidget.triggerTripleClick()
            }
        }
    }

    // ==================== WIDGET PALETTE (external window, right of main window) ====================
    Window {
        id: paletteWindow
        transientParent: root.mainWindow
        x: root.mainWindow ? (root.mainWindow.x + root.mainWindow.width) : 0
        y: root.mainWindow ? root.mainWindow.y : 0
        width: 240
        height: root.mainWindow ? root.mainWindow.height : 600
        visible: root.editMode && root.mainWindow !== null
        flags: Qt.Tool | Qt.FramelessWindowHint
        color: "transparent"

        Rectangle {
            anchors.fill: parent
            color: "#2a2a2a"
            border.color: "#4a9eff"
            border.width: 1

            Rectangle {
                id: paletteTitleBar
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                height: 28
                color: "#1e3a5a"

                Text {
                    anchors.centerIn: parent
                    text: "Widget Palette"
                    color: "#ccc"
                    font.pixelSize: 12
                    font.bold: true
                }
            }

            Comp.WidgetPalette {
                id: widgetPalette
                anchors.top: paletteTitleBar.bottom
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: 2
                editMode: root.editMode
                widgetModel: root.widgetModel
                outputMode: root.outputMode

                onAddWidget: function(widgetData) {
                    root._addWidget(widgetData)
                    root._autoSave()
                }

                onSaveLayout: {
                    if (controller && controller.isBundledProfile()) {
                        saveAsDialog.visible = true
                        saveAsNameField.text = ""
                        saveAsNameField.forceActiveFocus()
                    } else {
                        root._saveLayout()
                    }
                }

                onToggleGrid: {
                    root.showGrid = !root.showGrid
                    gridCanvas.requestPaint()
                }

                onDoneEditing: {
                    root.editMode = false
                    root._saveLayout()
                }

                onSaveLayoutAs: function(layoutName) {
                    saveAsDialog.visible = true
                    saveAsNameField.text = ""
                    saveAsNameField.forceActiveFocus()
                }
            }
        }
    }

    // Edit Layout is now in Settings menu (Main.qml)

    // ==================== WIDGET CONFIG DIALOG ====================
    Rectangle {
        id: configDialog
        anchors.centerIn: parent
        width: 360
        height: Math.min(configContent.height + 60, root.height - 40)
        radius: 12
        color: "#2a2a2a"
        border.color: "#4a9eff"
        border.width: 2
        visible: false
        z: 500
        clip: true

        property string targetWidgetId: ""
        property var targetWidget: null
        property var _currentMacroConfig: ({ "zones": {}, "deadzone_percent": 30, "diagonal_mode": "8-way" })

        function openForWidget(wid) {
            for (var i = 0; i < root.widgetModel.length; i++) {
                if (root.widgetModel[i].id === wid) {
                    targetWidgetId = wid
                    targetWidget = root.widgetModel[i]
                    labelField.text = targetWidget.label || ""

                    // Sensitivity & deadzone (all axis types, percentage-based)
                    sensitivitySlider.value = targetWidget.sensitivity !== undefined ? targetWidget.sensitivity : 50.0
                    deadZoneSlider.value = targetWidget.dead_zone !== undefined ? targetWidget.dead_zone : 0.0
                    extremityDzSlider.value = targetWidget.extremity_dead_zone !== undefined ? targetWidget.extremity_dead_zone : 5.0

                    if (targetWidget.type === "button") {
                        buttonIdField.text = String(targetWidget.button_id || 1)
                        colorField.text = targetWidget.color || "#333"
                        shapeCombo.currentIndex = ["circle", "rounded", "square"].indexOf(targetWidget.shape || "rounded")
                        toggleModeSwitch.checked = targetWidget.toggle_mode || false
                    }
                    if (targetWidget.type === "joystick") {
                        var _axVals = root.outputMode === "vigem"
                            ? ["none", "x", "y", "rx", "ry", "z", "rz"]
                            : ["none", "x", "y", "rx", "ry", "z", "rz", "sl0", "sl1"]
                        var _hIdx = _axVals.indexOf(targetWidget.mapping.axis_x || "x")
                        axisXCombo.currentIndex = _hIdx >= 0 ? _hIdx : 1
                        var _vIdx = _axVals.indexOf(targetWidget.mapping.axis_y || "y")
                        axisYCombo.currentIndex = _vIdx >= 0 ? _vIdx : 2
                        invertXBtn.inverted = targetWidget.invert_x || false
                        invertYBtn.inverted = targetWidget.invert_y || false
                        tripleClickSwitch.checked = targetWidget.triple_click_enabled !== undefined ? targetWidget.triple_click_enabled : true
                        autoCenterSwitch.checked = targetWidget.auto_center || false
                        autoCenterDelaySlider.value = targetWidget.auto_center_delay !== undefined ? Math.max(1, Math.min(10, targetWidget.auto_center_delay)) : 5
                        lockSensSlider.value = targetWidget.lock_sensitivity !== undefined ? targetWidget.lock_sensitivity : 4
                        tremorFilterSlider.value = targetWidget.tremor_filter !== undefined ? targetWidget.tremor_filter : 0
                        macroModeSwitch.checked = targetWidget.macro_mode || false
                        configDialog._currentMacroConfig = targetWidget.macro_config || { "zones": {}, "deadzone_percent": 30, "diagonal_mode": "8-way" }
                    }
                    if (targetWidget.type === "slider" || targetWidget.type === "wheel") {
                        var slAxis = targetWidget.mapping.axis || "z"
                        sliderAxisCombo.currentIndex = ["z", "rz", "sl0", "sl1", "x", "y", "rx", "ry"].indexOf(slAxis)
                        if (sliderAxisCombo.currentIndex < 0) sliderAxisCombo.currentIndex = 0
                        if (targetWidget.type === "slider") {
                            snapModeCombo.currentIndex = ["none", "left", "center"].indexOf(targetWidget.snap_mode || "none")
                            if (snapModeCombo.currentIndex < 0) snapModeCombo.currentIndex = 0
                            clickModeCombo.currentIndex = ["jump", "relative"].indexOf(targetWidget.click_mode || "jump")
                            if (clickModeCombo.currentIndex < 0) clickModeCombo.currentIndex = 0
                        }
                    }
                    if (targetWidget.type === "dpad") {
                        dpadUpIdField.text = String(targetWidget.mapping.up || 1)
                        dpadDownIdField.text = String(targetWidget.mapping.down || 2)
                        dpadLeftIdField.text = String(targetWidget.mapping.left || 3)
                        dpadRightIdField.text = String(targetWidget.mapping.right || 4)
                    }
                    visible = true
                    return
                }
            }
        }

        // Dim background
        Rectangle {
            parent: root
            anchors.fill: parent
            color: "#00000088"
            visible: configDialog.visible
            z: 499

            MouseArea {
                anchors.fill: parent
                onClicked: configDialog.visible = false
            }
        }

        Flickable {
            id: configFlickable
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: dialogButtonRow.top
            anchors.margins: 10
            contentHeight: configContent.height
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            flickableDirection: Flickable.VerticalFlick

            ScrollBar.vertical: Basic.ScrollBar {
                policy: configFlickable.contentHeight > configFlickable.height ? Basic.ScrollBar.AlwaysOn : Basic.ScrollBar.AsNeeded
                width: 8
                contentItem: Rectangle {
                    implicitWidth: 8
                    radius: 4
                    color: parent.pressed ? "#888" : (parent.hovered ? "#777" : "#555")
                }
                background: Rectangle {
                    implicitWidth: 8
                    radius: 4
                    color: "#2a2a2a"
                }
            }

        Column {
            id: configContent
            width: configFlickable.width
            spacing: 10

            Text {
                text: "Configure Widget"
                color: "#fff"
                font.pixelSize: 16
                font.bold: true
            }

            Rectangle { width: parent.width; height: 1; color: "#444" }

            // Label
            Row {
                spacing: 8
                Text { text: "Label:"; color: "#ccc"; font.pixelSize: 12; width: 70; verticalAlignment: Text.AlignVCenter; height: 30 }
                Rectangle {
                    width: 200; height: 30; radius: 4; color: "#1a1a1a"; border.color: "#444"
                    TextInput {
                        id: labelField
                        anchors.fill: parent
                        anchors.margins: 6
                        color: "white"
                        font.pixelSize: 12
                    }
                }
            }

            // Button-specific fields
            Column {
                spacing: 8
                visible: configDialog.targetWidget ? configDialog.targetWidget.type === "button" : false
                width: parent.width

                // ViGEm mode: read-only Xbox button assignment
                Row {
                    visible: root.outputMode === "vigem"
                    spacing: 8
                    Text { text: "Xbox Button:"; color: "#ccc"; font.pixelSize: 12; width: 82; verticalAlignment: Text.AlignVCenter; height: 32 }
                    Rectangle {
                        width: 80; height: 32; radius: 6
                        color: configDialog.targetWidget ? (configDialog.targetWidget.color || "#333") : "#333"
                        border.color: "#888"
                        border.width: 1
                        Text {
                            anchors.centerIn: parent
                            text: configDialog.targetWidget ? (configDialog.targetWidget.label || "?") : "?"
                            color: "white"
                            font.pixelSize: 13
                            font.bold: true
                        }
                    }
                    Text { text: "(fixed)"; color: "#666"; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter; height: 32 }
                }

                // vJoy mode: editable numeric button ID
                Row {
                    visible: root.outputMode !== "vigem"
                    spacing: 8
                    Text { text: "Button ID:"; color: "#ccc"; font.pixelSize: 12; width: 70; verticalAlignment: Text.AlignVCenter; height: 30 }
                    Rectangle {
                        width: 80; height: 30; radius: 4; color: "#1a1a1a"; border.color: "#444"
                        TextInput {
                            id: buttonIdField
                            anchors.fill: parent
                            anchors.margins: 6
                            color: "white"
                            font.pixelSize: 12
                            validator: IntValidator { bottom: 1; top: 128 }
                        }
                    }
                    Text { text: "(1-128)"; color: "#888"; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter; height: 30 }
                }

                Row {
                    spacing: 8
                    Text { text: "Color:"; color: "#ccc"; font.pixelSize: 12; width: 70; verticalAlignment: Text.AlignVCenter; height: 30 }
                    Rectangle {
                        width: 120; height: 30; radius: 4; color: "#1a1a1a"; border.color: "#444"
                        TextInput {
                            id: colorField
                            anchors.fill: parent
                            anchors.margins: 6
                            color: "white"
                            font.pixelSize: 12
                        }
                    }
                    Rectangle {
                        width: 30; height: 30; radius: 4
                        color: colorField.text || "#333"
                        border.color: "#666"
                        border.width: 1
                    }
                }

                Row {
                    spacing: 8
                    Text { text: "Shape:"; color: "#ccc"; font.pixelSize: 12; width: 70; verticalAlignment: Text.AlignVCenter; height: 30 }
                    Basic.ComboBox {
                        id: shapeCombo
                        width: 120
                        model: ["circle", "rounded", "square"]
                        background: Rectangle { color: "#1a1a1a"; border.color: "#444"; radius: 4 }
                        contentItem: Text { text: shapeCombo.displayText; color: "white"; font.pixelSize: 12; leftPadding: 8; verticalAlignment: Text.AlignVCenter }
                        delegate: ItemDelegate {
                            width: shapeCombo.width
                            highlighted: shapeCombo.highlightedIndex === index
                            contentItem: Text { text: modelData; color: parent.highlighted ? "white" : "#ccc"; font.pixelSize: 12; leftPadding: 8; verticalAlignment: Text.AlignVCenter }
                            background: Rectangle { color: parent.highlighted ? "#4a9eff" : "#333" }
                        }
                        popup: Popup {
                            y: shapeCombo.height; width: shapeCombo.width; padding: 1
                            background: Rectangle { color: "#333"; border.color: "#555"; radius: 4 }
                            contentItem: ListView { clip: true; implicitHeight: contentHeight; model: shapeCombo.delegateModel; currentIndex: shapeCombo.highlightedIndex }
                        }
                    }
                }

                // Toggle mode
                Row {
                    spacing: 8
                    Text { text: "Mode:"; color: "#ccc"; font.pixelSize: 12; width: 70; verticalAlignment: Text.AlignVCenter; height: 30 }
                    Rectangle {
                        width: 180; height: 30; radius: 4; color: "transparent"
                        Row {
                            spacing: 8
                            anchors.verticalCenter: parent.verticalCenter
                            Rectangle {
                                id: toggleModeSwitch
                                property bool checked: false
                                width: 44; height: 22; radius: 11
                                color: checked ? "#16a34a" : "#555"
                                Rectangle {
                                    width: 18; height: 18; radius: 9
                                    color: "white"
                                    x: parent.checked ? parent.width - width - 2 : 2
                                    anchors.verticalCenter: parent.verticalCenter
                                    Behavior on x { NumberAnimation { duration: 120 } }
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    onClicked: parent.checked = !parent.checked
                                }
                            }
                            Text {
                                text: toggleModeSwitch.checked ? "Toggle" : "Momentary"
                                color: toggleModeSwitch.checked ? "#4ade80" : "#aaa"
                                font.pixelSize: 11
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }
                    }
                }
            }

            // Joystick-specific fields
            Column {
                spacing: 8
                visible: configDialog.targetWidget ? configDialog.targetWidget.type === "joystick" : false
                width: parent.width

                Row {
                    spacing: 8
                    Text { text: "X-Axis:"; color: "#ccc"; font.pixelSize: 12; width: 70; verticalAlignment: Text.AlignVCenter; height: 30 }
                    Basic.ComboBox {
                        id: axisXCombo
                        width: 160
                        model: root.outputMode === "vigem"
                            ? ["None", "x (Left X)", "y (Left Y)", "rx (Right X)", "ry (Right Y)", "z (LT)", "rz (RT)"]
                            : ["None", "x (Left X)", "y (Left Y)", "rx (Right X)", "ry (Right Y)", "z (LT)", "rz (RT)", "sl0", "sl1"]
                        background: Rectangle { color: "#1a1a1a"; border.color: "#444"; radius: 4 }
                        contentItem: Text { text: axisXCombo.displayText; color: "white"; font.pixelSize: 12; leftPadding: 8; verticalAlignment: Text.AlignVCenter }
                        delegate: ItemDelegate {
                            width: axisXCombo.width
                            highlighted: axisXCombo.highlightedIndex === index
                            contentItem: Text { text: modelData; color: parent.highlighted ? "white" : "#ccc"; font.pixelSize: 12; leftPadding: 8; verticalAlignment: Text.AlignVCenter }
                            background: Rectangle { color: parent.highlighted ? "#4a9eff" : "#333" }
                        }
                        popup: Popup {
                            y: axisXCombo.height; width: axisXCombo.width; padding: 1
                            background: Rectangle { color: "#333"; border.color: "#555"; radius: 4 }
                            contentItem: ListView { clip: true; implicitHeight: contentHeight; model: axisXCombo.delegateModel; currentIndex: axisXCombo.highlightedIndex }
                        }
                    }
                    Rectangle {
                        id: invertXBtn
                        property bool inverted: false
                        width: 36; height: 30; radius: 4
                        color: inverted ? "#4a9eff" : "#2a2a2a"
                        border.color: inverted ? "#6ab8ff" : "#555"
                        Text { anchors.centerIn: parent; text: "INV"; color: parent.inverted ? "white" : "#888"; font.pixelSize: 10; font.bold: true }
                        MouseArea { anchors.fill: parent; onClicked: parent.inverted = !parent.inverted; cursorShape: Qt.PointingHandCursor }
                    }
                }

                Row {
                    spacing: 8
                    Text { text: "Y-Axis:"; color: "#ccc"; font.pixelSize: 12; width: 70; verticalAlignment: Text.AlignVCenter; height: 30 }
                    Basic.ComboBox {
                        id: axisYCombo
                        width: 160
                        model: root.outputMode === "vigem"
                            ? ["None", "x (Left X)", "y (Left Y)", "rx (Right X)", "ry (Right Y)", "z (LT)", "rz (RT)"]
                            : ["None", "x (Left X)", "y (Left Y)", "rx (Right X)", "ry (Right Y)", "z (LT)", "rz (RT)", "sl0", "sl1"]
                        background: Rectangle { color: "#1a1a1a"; border.color: "#444"; radius: 4 }
                        contentItem: Text { text: axisYCombo.displayText; color: "white"; font.pixelSize: 12; leftPadding: 8; verticalAlignment: Text.AlignVCenter }
                        delegate: ItemDelegate {
                            width: axisYCombo.width
                            highlighted: axisYCombo.highlightedIndex === index
                            contentItem: Text { text: modelData; color: parent.highlighted ? "white" : "#ccc"; font.pixelSize: 12; leftPadding: 8; verticalAlignment: Text.AlignVCenter }
                            background: Rectangle { color: parent.highlighted ? "#4a9eff" : "#333" }
                        }
                        popup: Popup {
                            y: axisYCombo.height; width: axisYCombo.width; padding: 1
                            background: Rectangle { color: "#333"; border.color: "#555"; radius: 4 }
                            contentItem: ListView { clip: true; implicitHeight: contentHeight; model: axisYCombo.delegateModel; currentIndex: axisYCombo.highlightedIndex }
                        }
                    }
                    Rectangle {
                        id: invertYBtn
                        property bool inverted: false
                        width: 36; height: 30; radius: 4
                        color: inverted ? "#4a9eff" : "#2a2a2a"
                        border.color: inverted ? "#6ab8ff" : "#555"
                        Text { anchors.centerIn: parent; text: "INV"; color: parent.inverted ? "white" : "#888"; font.pixelSize: 10; font.bold: true }
                        MouseArea { anchors.fill: parent; onClicked: parent.inverted = !parent.inverted; cursorShape: Qt.PointingHandCursor }
                    }
                }

                // Triple-click lock toggle
                Row {
                    spacing: 8
                    Text { text: "Lock:"; color: "#ccc"; font.pixelSize: 12; width: 70; verticalAlignment: Text.AlignVCenter; height: 30 }
                    Rectangle {
                        width: 200; height: 30; radius: 4; color: "transparent"
                        Row {
                            spacing: 8
                            anchors.verticalCenter: parent.verticalCenter
                            Rectangle {
                                id: tripleClickSwitch
                                property bool checked: true
                                width: 44; height: 22; radius: 11
                                color: checked ? "#d45500" : "#555"
                                Rectangle {
                                    width: 18; height: 18; radius: 9; color: "white"
                                    x: parent.checked ? parent.width - width - 2 : 2
                                    anchors.verticalCenter: parent.verticalCenter
                                    Behavior on x { NumberAnimation { duration: 120 } }
                                }
                                MouseArea { anchors.fill: parent; onClicked: parent.checked = !parent.checked }
                            }
                            Text {
                                text: tripleClickSwitch.checked ? "Triple-click lock ON" : "Triple-click lock OFF"
                                color: tripleClickSwitch.checked ? "#ff8833" : "#aaa"
                                font.pixelSize: 11
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }
                    }
                }

                // Auto-center toggle (return to center when mouse stops in locked mode)
                Row {
                    spacing: 8
                    Text { text: "Return:"; color: "#ccc"; font.pixelSize: 12; width: 70; verticalAlignment: Text.AlignVCenter; height: 30 }
                    Rectangle {
                        width: 200; height: 30; radius: 4; color: "transparent"
                        Row {
                            spacing: 8
                            anchors.verticalCenter: parent.verticalCenter
                            Rectangle {
                                id: autoCenterSwitch
                                property bool checked: false
                                width: 44; height: 22; radius: 11
                                color: checked ? "#0078d4" : "#555"
                                Rectangle {
                                    width: 18; height: 18; radius: 9; color: "white"
                                    x: parent.checked ? parent.width - width - 2 : 2
                                    anchors.verticalCenter: parent.verticalCenter
                                    Behavior on x { NumberAnimation { duration: 120 } }
                                }
                                MouseArea { anchors.fill: parent; onClicked: parent.checked = !parent.checked }
                            }
                            Text {
                                text: autoCenterSwitch.checked ? "Auto-return to center when locked" : "Auto-return to center OFF"
                                color: autoCenterSwitch.checked ? "#4a9eff" : "#aaa"
                                font.pixelSize: 11
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }
                    }
                }

                // Lock sensitivity slider (how responsive the locked joystick is)
                Row {
                    spacing: 8
                    visible: tripleClickSwitch.checked
                    Text { text: "Lock Sens:"; color: "#ccc"; font.pixelSize: 12; width: 70; verticalAlignment: Text.AlignVCenter; height: 30 }
                    Basic.Slider {
                        id: lockSensSlider
                        width: 140
                        from: 1; to: 10; stepSize: 1
                        value: 4
                        background: Rectangle {
                            x: lockSensSlider.leftPadding; y: lockSensSlider.topPadding + lockSensSlider.availableHeight / 2 - height / 2
                            width: lockSensSlider.availableWidth; height: 4; radius: 2; color: "#444"
                            Rectangle { width: lockSensSlider.visualPosition * parent.width; height: parent.height; radius: 2; color: "#ff8833" }
                        }
                        handle: Rectangle { x: lockSensSlider.leftPadding + lockSensSlider.visualPosition * (lockSensSlider.availableWidth - width); y: lockSensSlider.topPadding + lockSensSlider.availableHeight / 2 - height / 2; width: 16; height: 16; radius: 8; color: "#fff" }
                    }
                    Text { text: lockSensSlider.value.toFixed(0); color: "#ff8833"; font.pixelSize: 11; width: 30; verticalAlignment: Text.AlignVCenter; height: 30 }
                }

                // Tremor filter slider (smooths jittery wheelchair joystick input)
                Row {
                    spacing: 8
                    visible: tripleClickSwitch.checked
                    Text { text: "Tremor:"; color: "#ccc"; font.pixelSize: 12; width: 70; verticalAlignment: Text.AlignVCenter; height: 30 }
                    Basic.Slider {
                        id: tremorFilterSlider
                        width: 140
                        from: 0; to: 10; stepSize: 1
                        value: 0
                        background: Rectangle {
                            x: tremorFilterSlider.leftPadding; y: tremorFilterSlider.topPadding + tremorFilterSlider.availableHeight / 2 - height / 2
                            width: tremorFilterSlider.availableWidth; height: 4; radius: 2; color: "#444"
                            Rectangle { width: tremorFilterSlider.visualPosition * parent.width; height: parent.height; radius: 2; color: "#66bb6a" }
                        }
                        handle: Rectangle { x: tremorFilterSlider.leftPadding + tremorFilterSlider.visualPosition * (tremorFilterSlider.availableWidth - width); y: tremorFilterSlider.topPadding + tremorFilterSlider.availableHeight / 2 - height / 2; width: 16; height: 16; radius: 8; color: "#fff" }
                    }
                    Text { text: tremorFilterSlider.value > 0 ? tremorFilterSlider.value.toFixed(0) : "Off"; color: "#66bb6a"; font.pixelSize: 11; width: 30; verticalAlignment: Text.AlignVCenter; height: 30 }
                }

                // Auto-center delay slider (only visible when auto-center is on)
                Row {
                    spacing: 8
                    visible: autoCenterSwitch.checked
                    Text { text: "Delay:"; color: "#ccc"; font.pixelSize: 12; width: 70; verticalAlignment: Text.AlignVCenter; height: 30 }
                    Basic.Slider {
                        id: autoCenterDelaySlider
                        width: 140
                        from: 1; to: 10; stepSize: 1
                        value: 5
                        background: Rectangle {
                            x: autoCenterDelaySlider.leftPadding; y: autoCenterDelaySlider.topPadding + autoCenterDelaySlider.availableHeight / 2 - height / 2
                            width: autoCenterDelaySlider.availableWidth; height: 4; radius: 2; color: "#444"
                            Rectangle { width: autoCenterDelaySlider.visualPosition * parent.width; height: parent.height; radius: 2; color: "#0078d4" }
                        }
                        handle: Rectangle { x: autoCenterDelaySlider.leftPadding + autoCenterDelaySlider.visualPosition * (autoCenterDelaySlider.availableWidth - width); y: autoCenterDelaySlider.topPadding + autoCenterDelaySlider.availableHeight / 2 - height / 2; width: 16; height: 16; radius: 8; color: "#fff" }
                    }
                    Text { text: autoCenterDelaySlider.value.toFixed(0) + "ms"; color: "#0078d4"; font.pixelSize: 11; width: 40; verticalAlignment: Text.AlignVCenter; height: 30 }
                }

                // Macro mode section
                Rectangle { width: parent.width; height: 1; color: "#444"; visible: !macroModeSwitch.checked }

                Row {
                    spacing: 8
                    Text { text: "Macro:"; color: "#ccc"; font.pixelSize: 12; width: 70; verticalAlignment: Text.AlignVCenter; height: 30 }
                    Rectangle {
                        width: 200; height: 30; radius: 4; color: "transparent"
                        Row {
                            spacing: 8
                            anchors.verticalCenter: parent.verticalCenter
                            Rectangle {
                                id: macroModeSwitch
                                property bool checked: false
                                width: 44; height: 22; radius: 11
                                color: checked ? "#e040fb" : "#555"
                                Rectangle {
                                    width: 18; height: 18; radius: 9; color: "white"
                                    x: parent.checked ? parent.width - width - 2 : 2
                                    anchors.verticalCenter: parent.verticalCenter
                                    Behavior on x { NumberAnimation { duration: 120 } }
                                }
                                MouseArea { anchors.fill: parent; onClicked: parent.checked = !parent.checked }
                            }
                            Text {
                                text: macroModeSwitch.checked ? "Macro Mode ON" : "Macro Mode OFF"
                                color: macroModeSwitch.checked ? "#e040fb" : "#aaa"
                                font.pixelSize: 11
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }
                    }
                }

                // Edit Macro Mappings button (only visible when macro mode is on)
                Rectangle {
                    visible: macroModeSwitch.checked
                    width: 200; height: 32; radius: 6
                    color: editMacroMa.containsMouse ? "#9c27b0" : "#7b1fa2"
                    anchors.horizontalCenter: parent.horizontalCenter

                    Text {
                        anchors.centerIn: parent
                        text: "Edit Macro Mappings..."
                        color: "white"
                        font.pixelSize: 12
                        font.bold: true
                    }

                    MouseArea {
                        id: editMacroMa
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: {
                            // Load current macro config into editor
                            var cfg = configDialog.targetWidget ? configDialog.targetWidget.macro_config : null
                            macroEditorDialog.loadConfig(cfg || { "zones": {}, "deadzone_percent": 30, "diagonal_mode": "8-way" })
                            macroEditorDialog.visible = true
                        }
                    }
                }

                Text {
                    visible: macroModeSwitch.checked
                    text: "Macro mode converts joystick directions into button presses, axis outputs, or turbo actions."
                    color: "#888"
                    font.pixelSize: 9
                    wrapMode: Text.WordWrap
                    width: parent.width - 20
                }
            }

            // D-Pad specific fields
            Column {
                spacing: 8
                visible: configDialog.targetWidget ? configDialog.targetWidget.type === "dpad" : false
                width: parent.width

                Text { text: "Button IDs (1-128):"; color: "#aaa"; font.pixelSize: 11; font.bold: true }

                Row {
                    spacing: 8
                    Text { text: "Up:"; color: "#ccc"; font.pixelSize: 12; width: 50; verticalAlignment: Text.AlignVCenter; height: 28 }
                    Rectangle {
                        width: 50; height: 28; radius: 4; color: "#1a1a1a"; border.color: "#444"
                        TextInput { id: dpadUpIdField; anchors.fill: parent; anchors.margins: 4; color: "white"; font.pixelSize: 12; validator: IntValidator { bottom: 1; top: 128 } }
                    }
                    Text { text: "Down:"; color: "#ccc"; font.pixelSize: 12; width: 50; verticalAlignment: Text.AlignVCenter; height: 28 }
                    Rectangle {
                        width: 50; height: 28; radius: 4; color: "#1a1a1a"; border.color: "#444"
                        TextInput { id: dpadDownIdField; anchors.fill: parent; anchors.margins: 4; color: "white"; font.pixelSize: 12; validator: IntValidator { bottom: 1; top: 128 } }
                    }
                }
                Row {
                    spacing: 8
                    Text { text: "Left:"; color: "#ccc"; font.pixelSize: 12; width: 50; verticalAlignment: Text.AlignVCenter; height: 28 }
                    Rectangle {
                        width: 50; height: 28; radius: 4; color: "#1a1a1a"; border.color: "#444"
                        TextInput { id: dpadLeftIdField; anchors.fill: parent; anchors.margins: 4; color: "white"; font.pixelSize: 12; validator: IntValidator { bottom: 1; top: 128 } }
                    }
                    Text { text: "Right:"; color: "#ccc"; font.pixelSize: 12; width: 50; verticalAlignment: Text.AlignVCenter; height: 28 }
                    Rectangle {
                        width: 50; height: 28; radius: 4; color: "#1a1a1a"; border.color: "#444"
                        TextInput { id: dpadRightIdField; anchors.fill: parent; anchors.margins: 4; color: "white"; font.pixelSize: 12; validator: IntValidator { bottom: 1; top: 128 } }
                    }
                }
            }

            // Slider & Wheel axis fields
            Column {
                spacing: 8
                visible: configDialog.targetWidget ? (configDialog.targetWidget.type === "slider" || configDialog.targetWidget.type === "wheel") : false
                width: parent.width

                Row {
                    spacing: 8
                    Text { text: "Axis:"; color: "#ccc"; font.pixelSize: 12; width: 70; verticalAlignment: Text.AlignVCenter; height: 30 }
                    Basic.ComboBox {
                        id: sliderAxisCombo
                        width: 200
                        model: ["z (LT/Throttle)", "rz (RT/Rudder)", "sl0 (Slider 0)", "sl1 (Slider 1)", "x", "y", "rx", "ry"]
                        background: Rectangle { color: "#1a1a1a"; border.color: "#444"; radius: 4 }
                        contentItem: Text { text: sliderAxisCombo.displayText; color: "white"; font.pixelSize: 12; leftPadding: 8; verticalAlignment: Text.AlignVCenter }
                        delegate: ItemDelegate {
                            width: sliderAxisCombo.width
                            highlighted: sliderAxisCombo.highlightedIndex === index
                            contentItem: Text { text: modelData; color: parent.highlighted ? "white" : "#ccc"; font.pixelSize: 12; leftPadding: 8; verticalAlignment: Text.AlignVCenter }
                            background: Rectangle { color: parent.highlighted ? "#4a9eff" : "#333" }
                        }
                        popup: Popup {
                            y: sliderAxisCombo.height; width: sliderAxisCombo.width; padding: 1
                            background: Rectangle { color: "#333"; border.color: "#555"; radius: 4 }
                            contentItem: ListView { clip: true; implicitHeight: contentHeight; model: sliderAxisCombo.delegateModel; currentIndex: sliderAxisCombo.highlightedIndex }
                        }
                    }
                }

                // Snap mode (slider only)
                Row {
                    spacing: 8
                    visible: configDialog.targetWidget ? configDialog.targetWidget.type === "slider" : false
                    Text { text: "Snap:"; color: "#ccc"; font.pixelSize: 12; width: 70; verticalAlignment: Text.AlignVCenter; height: 30 }
                    Basic.ComboBox {
                        id: snapModeCombo
                        width: 200
                        model: ["None (hold position)", "Snap to zero", "Snap to center"]
                        background: Rectangle { color: "#1a1a1a"; border.color: "#444"; radius: 4 }
                        contentItem: Text { text: snapModeCombo.displayText; color: "white"; font.pixelSize: 12; leftPadding: 8; verticalAlignment: Text.AlignVCenter }
                        delegate: ItemDelegate {
                            width: snapModeCombo.width
                            highlighted: snapModeCombo.highlightedIndex === index
                            contentItem: Text { text: modelData; color: parent.highlighted ? "white" : "#ccc"; font.pixelSize: 12; leftPadding: 8; verticalAlignment: Text.AlignVCenter }
                            background: Rectangle { color: parent.highlighted ? "#4a9eff" : "#333" }
                        }
                        popup: Popup {
                            y: snapModeCombo.height; width: snapModeCombo.width; padding: 1
                            background: Rectangle { color: "#333"; border.color: "#555"; radius: 4 }
                            contentItem: ListView { clip: true; implicitHeight: contentHeight; model: snapModeCombo.delegateModel; currentIndex: snapModeCombo.highlightedIndex }
                        }
                    }
                }

                // Click mode (slider only)
                Row {
                    spacing: 8
                    visible: configDialog.targetWidget ? configDialog.targetWidget.type === "slider" : false
                    Text { text: "Click:"; color: "#ccc"; font.pixelSize: 12; width: 70; verticalAlignment: Text.AlignVCenter; height: 30 }
                    Basic.ComboBox {
                        id: clickModeCombo
                        width: 200
                        model: ["Jump to position", "Relative drag"]
                        background: Rectangle { color: "#1a1a1a"; border.color: "#444"; radius: 4 }
                        contentItem: Text { text: clickModeCombo.displayText; color: "white"; font.pixelSize: 12; leftPadding: 8; verticalAlignment: Text.AlignVCenter }
                        delegate: ItemDelegate {
                            width: clickModeCombo.width
                            highlighted: clickModeCombo.highlightedIndex === index
                            contentItem: Text { text: modelData; color: parent.highlighted ? "white" : "#ccc"; font.pixelSize: 12; leftPadding: 8; verticalAlignment: Text.AlignVCenter }
                            background: Rectangle { color: parent.highlighted ? "#4a9eff" : "#333" }
                        }
                        popup: Popup {
                            y: clickModeCombo.height; width: clickModeCombo.width; padding: 1
                            background: Rectangle { color: "#333"; border.color: "#555"; radius: 4 }
                            contentItem: ListView { clip: true; implicitHeight: contentHeight; model: clickModeCombo.delegateModel; currentIndex: clickModeCombo.highlightedIndex }
                        }
                    }
                }

                // Orientation display (read-only, set by widget type from palette)
                Row {
                    spacing: 8
                    visible: configDialog.targetWidget ? configDialog.targetWidget.type === "slider" : false
                    Text { text: "Orient:"; color: "#ccc"; font.pixelSize: 12; width: 70; verticalAlignment: Text.AlignVCenter; height: 30 }
                    Text {
                        text: configDialog.targetWidget ? (configDialog.targetWidget.orientation === "vertical" ? "Vertical" : "Horizontal") : "Horizontal"
                        color: "#aaa"; font.pixelSize: 12; verticalAlignment: Text.AlignVCenter; height: 30
                    }
                }
            }

            // Sensitivity & Deadzone (for axis widgets: joystick, slider, wheel)
            Column {
                spacing: 8
                visible: configDialog.targetWidget ? (configDialog.targetWidget.type === "joystick" || configDialog.targetWidget.type === "slider" || configDialog.targetWidget.type === "wheel") : false
                width: parent.width

                Rectangle { width: parent.width; height: 1; color: "#444" }

                Text { text: "Sensitivity Settings"; color: "#aaa"; font.pixelSize: 11; font.bold: true }

                // Copy sensitivity from another widget
                Row {
                    spacing: 8
                    Text { text: "Copy from:"; color: "#ccc"; font.pixelSize: 12; width: 70; verticalAlignment: Text.AlignVCenter; height: 30 }
                    Basic.ComboBox {
                        id: copySensCombo
                        width: 200
                        property var axisWidgets: {
                            var list = ["-- Select --"]
                            if (!root.widgetModel) return list
                            for (var i = 0; i < root.widgetModel.length; i++) {
                                var w = root.widgetModel[i]
                                if ((w.type === "joystick" || w.type === "slider" || w.type === "wheel") && w.id !== configDialog.targetWidgetId) {
                                    list.push((w.label || w.type) + " (" + w.id.substring(0,6) + ")")
                                }
                            }
                            return list
                        }
                        property var axisWidgetIds: {
                            var ids = [null]
                            if (!root.widgetModel) return ids
                            for (var i = 0; i < root.widgetModel.length; i++) {
                                var w = root.widgetModel[i]
                                if ((w.type === "joystick" || w.type === "slider" || w.type === "wheel") && w.id !== configDialog.targetWidgetId) {
                                    ids.push(w.id)
                                }
                            }
                            return ids
                        }
                        model: axisWidgets
                        currentIndex: 0
                        onCurrentIndexChanged: {
                            if (currentIndex <= 0) return
                            var srcId = axisWidgetIds[currentIndex]
                            if (!srcId) return
                            for (var i = 0; i < root.widgetModel.length; i++) {
                                if (root.widgetModel[i].id === srcId) {
                                    var src = root.widgetModel[i]
                                    sensitivitySlider.value = src.sensitivity !== undefined ? src.sensitivity : 50
                                    deadZoneSlider.value = src.dead_zone !== undefined ? src.dead_zone : 0
                                    extremityDzSlider.value = src.extremity_dead_zone !== undefined ? src.extremity_dead_zone : 5
                                    break
                                }
                            }
                            currentIndex = 0
                        }
                        background: Rectangle { color: "#1a1a1a"; border.color: "#444"; radius: 4 }
                        contentItem: Text { text: copySensCombo.displayText; color: "white"; font.pixelSize: 12; leftPadding: 8; verticalAlignment: Text.AlignVCenter }
                        delegate: ItemDelegate {
                            width: copySensCombo.width
                            highlighted: copySensCombo.highlightedIndex === index
                            contentItem: Text { text: modelData; color: parent.highlighted ? "white" : "#ccc"; font.pixelSize: 12; leftPadding: 8; verticalAlignment: Text.AlignVCenter }
                            background: Rectangle { color: parent.highlighted ? "#4a9eff" : "#333" }
                        }
                        popup: Popup {
                            y: copySensCombo.height; width: copySensCombo.width; padding: 1
                            background: Rectangle { color: "#333"; border.color: "#555"; radius: 4 }
                            contentItem: ListView { clip: true; implicitHeight: contentHeight; model: copySensCombo.delegateModel; currentIndex: copySensCombo.highlightedIndex }
                        }
                    }
                }

                // Sensitivity slider (0-100%, 50% = linear)
                Row {
                    spacing: 8
                    Text { text: "Sensitivity:"; color: "#ccc"; font.pixelSize: 12; width: 70; verticalAlignment: Text.AlignVCenter; height: 30 }
                    Basic.Slider {
                        id: sensitivitySlider
                        width: 140
                        from: 0; to: 100; stepSize: 1
                        value: 50
                        onValueChanged: responseCurve.requestPaint()
                        background: Rectangle {
                            x: sensitivitySlider.leftPadding; y: sensitivitySlider.topPadding + sensitivitySlider.availableHeight / 2 - height / 2
                            width: sensitivitySlider.availableWidth; height: 4; radius: 2; color: "#444"
                            Rectangle { width: sensitivitySlider.visualPosition * parent.width; height: parent.height; radius: 2; color: "#4a9eff" }
                        }
                        handle: Rectangle { x: sensitivitySlider.leftPadding + sensitivitySlider.visualPosition * (sensitivitySlider.availableWidth - width); y: sensitivitySlider.topPadding + sensitivitySlider.availableHeight / 2 - height / 2; width: 16; height: 16; radius: 8; color: "#fff" }
                    }
                    Text { text: sensitivitySlider.value.toFixed(0) + "%"; color: "#4a9eff"; font.pixelSize: 11; width: 35; verticalAlignment: Text.AlignVCenter; height: 30 }
                }

                // Dead zone slider (0-100%)
                Row {
                    spacing: 8
                    Text { text: "Deadzone:"; color: "#ccc"; font.pixelSize: 12; width: 70; verticalAlignment: Text.AlignVCenter; height: 30 }
                    Basic.Slider {
                        id: deadZoneSlider
                        width: 140
                        from: 0; to: 100; stepSize: 1
                        value: 0
                        onValueChanged: responseCurve.requestPaint()
                        background: Rectangle {
                            x: deadZoneSlider.leftPadding; y: deadZoneSlider.topPadding + deadZoneSlider.availableHeight / 2 - height / 2
                            width: deadZoneSlider.availableWidth; height: 4; radius: 2; color: "#444"
                            Rectangle { width: deadZoneSlider.visualPosition * parent.width; height: parent.height; radius: 2; color: "#ff6a00" }
                        }
                        handle: Rectangle { x: deadZoneSlider.leftPadding + deadZoneSlider.visualPosition * (deadZoneSlider.availableWidth - width); y: deadZoneSlider.topPadding + deadZoneSlider.availableHeight / 2 - height / 2; width: 16; height: 16; radius: 8; color: "#fff" }
                    }
                    Text { text: deadZoneSlider.value.toFixed(0) + "%"; color: "#ff6a00"; font.pixelSize: 11; width: 35; verticalAlignment: Text.AlignVCenter; height: 30 }
                }

                // Extremity dead zone slider (0-100%)
                Row {
                    spacing: 8
                    Text { text: "Extremity:"; color: "#ccc"; font.pixelSize: 12; width: 70; verticalAlignment: Text.AlignVCenter; height: 30 }
                    Basic.Slider {
                        id: extremityDzSlider
                        width: 140
                        from: 0; to: 100; stepSize: 1
                        value: 5
                        onValueChanged: responseCurve.requestPaint()
                        background: Rectangle {
                            x: extremityDzSlider.leftPadding; y: extremityDzSlider.topPadding + extremityDzSlider.availableHeight / 2 - height / 2
                            width: extremityDzSlider.availableWidth; height: 4; radius: 2; color: "#444"
                            Rectangle { width: extremityDzSlider.visualPosition * parent.width; height: parent.height; radius: 2; color: "#aa44ff" }
                        }
                        handle: Rectangle { x: extremityDzSlider.leftPadding + extremityDzSlider.visualPosition * (extremityDzSlider.availableWidth - width); y: extremityDzSlider.topPadding + extremityDzSlider.availableHeight / 2 - height / 2; width: 16; height: 16; radius: 8; color: "#fff" }
                    }
                    Text { text: extremityDzSlider.value.toFixed(0) + "%"; color: "#aa44ff"; font.pixelSize: 11; width: 35; verticalAlignment: Text.AlignVCenter; height: 30 }
                }

                // Response Curve Preview (matches Settings menu graph exactly)
                Text { text: "Response Curve Preview"; color: "#888"; font.pixelSize: 10 }
                Rectangle {
                    width: parent.width; height: 120
                    color: "#111"; border.color: "#444"; border.width: 1; radius: 4

                    Canvas {
                        id: responseCurve
                        anchors.fill: parent
                        anchors.margins: 4
                        onPaint: {
                            var ctx = getContext("2d")
                            ctx.clearRect(0, 0, width, height)
                            var w = width, h = height
                            // Convert from percentage to internal units (same as config.py)
                            var sens = sensitivitySlider.value / 100.0     // 0..1
                            var dz = (deadZoneSlider.value / 100.0) * 0.25 // 0..0.25
                            var edz = extremityDzSlider.value / 100.0      // 0..1

                            // Grid lines
                            ctx.strokeStyle = "#333"
                            ctx.lineWidth = 0.5
                            for (var g = 0; g <= 4; g++) {
                                var gx = w * g / 4
                                ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, h); ctx.stroke()
                                var gy = h * g / 4
                                ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(w, gy); ctx.stroke()
                            }

                            // Dead zone shaded region
                            if (dz > 0) {
                                ctx.fillStyle = "rgba(255,106,0,0.15)"
                                ctx.fillRect(0, 0, w * dz, h)
                            }

                            // Curve (exact same formula as apply_joystick_dialog_curve)
                            ctx.strokeStyle = "#4a9eff"
                            ctx.lineWidth = 2
                            ctx.beginPath()
                            var first = true
                            for (var i = 0; i <= 100; i++) {
                                var input = i / 100.0
                                var output
                                if (input < dz) {
                                    output = 0
                                } else {
                                    var availRange = 1.0 - dz
                                    var normalized = availRange > 0 ? (input - dz) / availRange : 1
                                    if (Math.abs(sens - 0.5) < 1e-9) {
                                        output = normalized
                                    } else if (sens < 0.5) {
                                        var power = 1.0 + (0.5 - sens) * 6.0
                                        output = Math.pow(normalized, power)
                                    } else {
                                        var power2 = 1.0 - (sens - 0.5) * 1.8
                                        output = Math.pow(normalized, Math.max(0.1, power2))
                                    }
                                    if (edz > 0) output *= (1.0 - edz)
                                }
                                var px = i / 100.0 * w
                                var py = h - output * h
                                if (first) { ctx.moveTo(px, py); first = false }
                                else ctx.lineTo(px, py)
                            }
                            ctx.stroke()

                            // Linear reference line (dashed)
                            ctx.strokeStyle = "#555"
                            ctx.lineWidth = 1
                            ctx.setLineDash([4, 4])
                            ctx.beginPath(); ctx.moveTo(0, h); ctx.lineTo(w, 0); ctx.stroke()
                            ctx.setLineDash([])
                        }
                    }
                }
            }

        }
        }  // close Flickable

        // Dialog buttons — always visible at bottom, outside Flickable
        Row {
            id: dialogButtonRow
            spacing: 10
            anchors.bottom: parent.bottom
            anchors.right: parent.right
            anchors.margins: 12

            Rectangle {
                width: 70; height: 32; radius: 6
                color: cancelConfigArea.containsMouse ? "#444" : "#333"
                border.color: "#555"
                Text { anchors.centerIn: parent; text: "Cancel"; color: "#ccc"; font.pixelSize: 12 }
                MouseArea { id: cancelConfigArea; anchors.fill: parent; hoverEnabled: true; onClicked: configDialog.visible = false }
            }

            Rectangle {
                width: 70; height: 32; radius: 6
                color: applyConfigArea.containsMouse ? "#1a8cff" : "#0078d4"
                Text { anchors.centerIn: parent; text: "Apply"; color: "white"; font.pixelSize: 12; font.bold: true }
                MouseArea {
                    id: applyConfigArea
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: {
                        var wid = configDialog.targetWidgetId
                        var wType = configDialog.targetWidget.type
                        _updateWidgetProp(wid, "label", labelField.text)

                        if (wType === "button") {
                            if (root.outputMode !== "vigem") {
                                _updateWidgetProp(wid, "button_id", parseInt(buttonIdField.text) || 1)
                            }
                            _updateWidgetProp(wid, "color", colorField.text)
                            _updateWidgetProp(wid, "shape", shapeCombo.currentText)
                            _updateWidgetProp(wid, "toggle_mode", toggleModeSwitch.checked)
                        }

                        if (wType === "joystick") {
                            var _axSave = root.outputMode === "vigem"
                                ? ["none", "x", "y", "rx", "ry", "z", "rz"]
                                : ["none", "x", "y", "rx", "ry", "z", "rz", "sl0", "sl1"]
                            var selAxisX = _axSave[axisXCombo.currentIndex] || "x"
                            var selAxisY = _axSave[axisYCombo.currentIndex] || "y"
                            _updateWidgetProp(wid, "mapping", {"axis_x": selAxisX, "axis_y": selAxisY})
                            _updateWidgetProp(wid, "invert_x", invertXBtn.inverted)
                            _updateWidgetProp(wid, "invert_y", invertYBtn.inverted)
                            _updateWidgetProp(wid, "triple_click_enabled", tripleClickSwitch.checked)
                            _updateWidgetProp(wid, "auto_center", autoCenterSwitch.checked)
                            _updateWidgetProp(wid, "auto_center_delay", autoCenterDelaySlider.value)
                            _updateWidgetProp(wid, "lock_sensitivity", lockSensSlider.value)
                            _updateWidgetProp(wid, "tremor_filter", tremorFilterSlider.value)
                            _updateWidgetProp(wid, "macro_mode", macroModeSwitch.checked)
                            _updateWidgetProp(wid, "macro_config", configDialog._currentMacroConfig)
                        }

                        if (wType === "slider" || wType === "wheel") {
                            var sliderAxes = ["z", "rz", "sl0", "sl1", "x", "y", "rx", "ry"]
                            _updateWidgetProp(wid, "mapping", {"axis": sliderAxes[sliderAxisCombo.currentIndex] || "z"})
                            if (wType === "slider") {
                                var snapModes = ["none", "left", "center"]
                                _updateWidgetProp(wid, "snap_mode", snapModes[snapModeCombo.currentIndex] || "none")
                                var clickModes = ["jump", "relative"]
                                _updateWidgetProp(wid, "click_mode", clickModes[clickModeCombo.currentIndex] || "jump")
                            }
                        }

                        if (wType === "dpad") {
                            _updateWidgetProp(wid, "mapping", {
                                "up": parseInt(dpadUpIdField.text) || 1,
                                "down": parseInt(dpadDownIdField.text) || 2,
                                "left": parseInt(dpadLeftIdField.text) || 3,
                                "right": parseInt(dpadRightIdField.text) || 4
                            })
                        }

                        // Save sensitivity & deadzone for axis widgets
                        if (wType === "joystick" || wType === "slider" || wType === "wheel") {
                            _updateWidgetProp(wid, "sensitivity", sensitivitySlider.value)
                            _updateWidgetProp(wid, "dead_zone", deadZoneSlider.value)
                            _updateWidgetProp(wid, "extremity_dead_zone", extremityDzSlider.value)
                        }

                        configDialog.visible = false
                        // Force model refresh — deep copy creates new objects so Repeater recreates delegates
                        root.widgetModel = JSON.parse(JSON.stringify(root.widgetModel))
                        _autoSave()
                    }
                }
            }
        }
    }

    // ==================== SAVE AS DIALOG ====================
    Rectangle {
        id: saveAsDialog
        anchors.centerIn: parent
        width: 320
        height: saveAsContent.height + 20
        radius: 12
        color: "#2a2a2a"
        border.color: "#4a9eff"
        border.width: 2
        visible: false
        z: 510

        // Dim background
        Rectangle {
            parent: root
            anchors.fill: parent
            color: "#00000088"
            visible: saveAsDialog.visible
            z: 509
            MouseArea { anchors.fill: parent; onClicked: saveAsDialog.visible = false }
        }

        Column {
            id: saveAsContent
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 16
            spacing: 12

            Text { text: "Save Layout As..."; color: "#fff"; font.pixelSize: 16; font.bold: true }
            Text { text: "Enter a name for this layout (e.g. game name):"; color: "#aaa"; font.pixelSize: 11 }

            Rectangle {
                width: parent.width; height: 34; radius: 4; color: "#1a1a1a"; border.color: "#444"
                TextInput {
                    id: saveAsNameField
                    anchors.fill: parent
                    anchors.margins: 8
                    color: "white"
                    font.pixelSize: 13
                }
            }

            Row {
                spacing: 10
                anchors.right: parent.right

                Rectangle {
                    width: 70; height: 32; radius: 6
                    color: saveAsCancelArea.containsMouse ? "#444" : "#333"
                    border.color: "#555"
                    Text { anchors.centerIn: parent; text: "Cancel"; color: "#ccc"; font.pixelSize: 12 }
                    MouseArea { id: saveAsCancelArea; anchors.fill: parent; hoverEnabled: true; onClicked: saveAsDialog.visible = false }
                }

                Rectangle {
                    width: 70; height: 32; radius: 6
                    color: saveAsOkArea.containsMouse ? "#1a8cff" : "#0078d4"
                    Text { anchors.centerIn: parent; text: "Save"; color: "white"; font.pixelSize: 12; font.bold: true }
                    MouseArea {
                        id: saveAsOkArea
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: {
                            var name = saveAsNameField.text.trim()
                            if (name.length > 0 && controller) {
                                controller.saveCustomLayoutAs(name, JSON.stringify(root.widgetModel), root.gridSnap, root.showGrid)
                            }
                            saveAsDialog.visible = false
                        }
                    }
                }
            }
        }
    }

    // ==================== MACRO EDITOR DIALOG ====================
    Rectangle {
        id: macroEditorOverlay
        parent: root
        anchors.fill: parent
        color: "#000000aa"
        visible: macroEditorDialog.visible
        z: 599
        MouseArea { anchors.fill: parent; onClicked: {} }
    }

    Comp.MacroEditorDialog {
        id: macroEditorDialog
        anchors.centerIn: parent
        visible: false
        z: 600

        onApplyRequested: function(config) {
            configDialog._currentMacroConfig = JSON.parse(JSON.stringify(config))
            macroEditorDialog.visible = false
        }

        onCancelRequested: {
            macroEditorDialog.visible = false
        }
    }

    // ==================== HELPER FUNCTIONS ====================
    function _updateWidgetProp(wid, prop, value) {
        for (var i = 0; i < widgetModel.length; i++) {
            if (widgetModel[i].id === wid) {
                widgetModel[i][prop] = value
                return
            }
        }
    }

    function _findFreePosition(w, h) {
        var canvasW = root.width > 0 ? root.width : 1024
        var canvasH = root.height > 0 ? root.height : 600
        var stepX = Math.max(Math.round(w / 2), gridSnap > 0 ? gridSnap : 10)
        var stepY = Math.max(Math.round(h / 2), gridSnap > 0 ? gridSnap : 10)
        for (var ty = 10; ty + h <= canvasH - 10; ty += stepY) {
            for (var tx = 10; tx + w <= canvasW - 10; tx += stepX) {
                var clear = true
                for (var i = 0; i < widgetModel.length; i++) {
                    var wm = widgetModel[i]
                    var wx1 = wm.x || 0, wy1 = wm.y || 0
                    var wx2 = wx1 + (wm.width || 100), wy2 = wy1 + (wm.height || 100)
                    if (tx < wx2 && tx + w > wx1 && ty < wy2 && ty + h > wy1) {
                        clear = false; break
                    }
                }
                if (clear) return { x: tx, y: ty }
            }
        }
        return { x: 10, y: 10 }  // fallback
    }

    function _addWidget(data) {
        var pos = _findFreePosition(data.width || 100, data.height || 100)
        data.x = pos.x
        data.y = pos.y
        var m = widgetModel.slice()
        m.push(data)
        widgetModel = m
    }

    function _removeWidget(wid) {
        var m = []
        for (var i = 0; i < widgetModel.length; i++) {
            if (widgetModel[i].id !== wid) {
                m.push(widgetModel[i])
            }
        }
        widgetModel = m
    }

    function _saveLayout() {
        if (controller) {
            controller.saveCustomLayout(JSON.stringify(widgetModel), gridSnap, showGrid)
        }
    }

    function _autoSave() {
        if (controller && editMode) {
            controller.saveCustomLayout(JSON.stringify(widgetModel), gridSnap, showGrid)
        }
    }
}
