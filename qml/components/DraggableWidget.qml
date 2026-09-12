import QtQuick 2.15
import QtQuick.Controls 2.15

Item {
    id: root

    // Widget data from profile (set by CustomLayout)
    property string widgetId: ""
    property string widgetType: "button"   // "joystick", "button", "slider", "dpad", "wheel"
    property string widgetLabel: ""
    property int buttonId: 1
    property string widgetColor: "#333"
    property string widgetShape: "rounded" // "circle", "rounded", "square"
    property string orientation: "horizontal"
    property var mapping: ({})
    property string snapMode: "none"       // Slider snap: "none" (hold), "left" (return to 0), "center" (spring to center)
    property string clickMode: "jump"      // Slider click: "jump" (teleport to click) or "relative" (drag from current)
    property bool toggleMode: false        // Button: toggle vs momentary (per-widget override)
    property bool tripleClickEnabled: true // Joystick: allow triple-click mouse lock
    property bool autoCenter: false         // Joystick/Wheel: auto-return to center when mouse stops (locked mode)
    property int autoCenterDelay: 5          // Joystick: ms to wait before auto-return starts (1-10)
    property real lockSensitivity: 4.0       // Joystick: lock mode sensitivity (1-10 UI, actual multiplier = value * 2)
    property real travelPx: 0                // Joystick: mouse pixels for full deflection; 0 = the drawn radius
    property string modifier: "none"         // Button: "none" sends a gamepad button, "precision" holds the aim modifier

    // Shaping (sensitivity, deadzones, anti-deadzone, tremor filter, precision
    // gain, inversion) lives in the bridge, which reads it from the profile by
    // widget id. This component sends raw geometry only.

    // Macro mode properties (joystick only)
    property bool macroMode: false          // When true, joystick acts as macro input instead of analog stick
    property var macroConfig: ({            // Configuration for macro zones
        "zones": {},
        "deadzone_percent": 30,
        "diagonal_mode": "8-way"
    })

    // Edit mode toggle (controlled by parent CustomLayout)
    property bool editMode: false

    // Grid snap size
    property int gridSnap: 10

    // Minimum widget sizes
    readonly property int minWidth: (widgetType === "button") ? 40 : (widgetType === "dpad" ? 100 : 80)
    readonly property int minHeight: (widgetType === "button") ? 40 : (widgetType === "dpad" ? 100 : 80)

    // Signals
    signal widgetMoved(string wid, real wx, real wy)
    signal widgetResized(string wid, real ww, real wh)
    signal widgetRemoved(string wid)
    signal widgetConfigRequested(string wid)
    signal mouseLockChanged(string wid, bool locked)

    // Joystick lock state (readable by parent for overlay)
    property bool joystickLocked: false

    // Public: parent overlay calls this to update joystick position during lock
    function updateJoystickPosition(nx, ny) {
        if (contentLoader.item && widgetType === "joystick") {
            var mag2 = nx * nx + ny * ny
            if (mag2 > 1.0) { var mag = Math.sqrt(mag2); nx /= mag; ny /= mag }
            contentLoader.item.xValue = nx
            contentLoader.item.yValue = ny
            contentLoader.item._sendJoystickValues(nx, ny)
        }
    }

    // Public: parent overlay calls this to check triple-click for unlock
    function triggerTripleClick() {
        if (contentLoader.item && widgetType === "joystick") {
            contentLoader.item._checkTripleClick()
        }
    }

    // Snap helper
    function snap(v) { return gridSnap > 0 ? Math.round(v / gridSnap) * gridSnap : v }

    // ==================== EDIT MODE OVERLAY ====================
    // Selection border when in edit mode
    Rectangle {
        anchors.fill: parent
        color: "transparent"
        border.color: editMode ? "#4a9eff" : "transparent"
        border.width: editMode ? 2 : 0
        radius: 4
        z: 100
        visible: editMode

        // Drag handle (entire widget is draggable in edit mode)
        MouseArea {
            id: dragArea
            anchors.fill: parent
            enabled: editMode
            cursorShape: editMode ? Qt.SizeAllCursor : Qt.ArrowCursor
            drag.target: root
            drag.axis: Drag.XAndYAxis
            drag.minimumX: 0
            drag.minimumY: 0
            drag.maximumX: root.parent ? root.parent.width - root.width : 9999
            drag.maximumY: root.parent ? root.parent.height - root.height : 9999

            onReleased: {
                root.x = snap(root.x)
                root.y = snap(root.y)
                root.widgetMoved(root.widgetId, root.x, root.y)
            }

            onDoubleClicked: {
                root.widgetConfigRequested(root.widgetId)
            }
        }

        // Widget label in edit mode
        Rectangle {
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.margins: -1
            width: editLabel.width + 10
            height: 18
            color: "#4a9eff"
            radius: 2
            z: 101

            Text {
                id: editLabel
                anchors.centerIn: parent
                text: root.widgetType.charAt(0).toUpperCase() + root.widgetType.slice(1) + (root.widgetLabel ? ": " + root.widgetLabel : "")
                color: "white"
                font.pixelSize: 10
                font.bold: true
            }
        }

        // Delete button (top-right corner)
        Rectangle {
            anchors.top: parent.top
            anchors.right: parent.right
            anchors.margins: -6
            width: 20
            height: 20
            radius: 10
            color: deleteArea.containsMouse ? "#ff4444" : "#cc3333"
            z: 102

            Text {
                anchors.centerIn: parent
                text: "×"
                color: "white"
                font.pixelSize: 14
                font.bold: true
            }

            MouseArea {
                id: deleteArea
                anchors.fill: parent
                hoverEnabled: true
                onClicked: root.widgetRemoved(root.widgetId)
            }
        }

        // Resize handle (bottom-right corner)
        Rectangle {
            id: resizeHandle
            anchors.bottom: parent.bottom
            anchors.right: parent.right
            anchors.margins: -4
            width: 14
            height: 14
            color: resizeArea.containsMouse ? "#4a9eff" : "#3a7acc"
            radius: 2
            z: 102

            Text {
                anchors.centerIn: parent
                text: "⇲"
                color: "white"
                font.pixelSize: 10
            }

            MouseArea {
                id: resizeArea
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.SizeFDiagCursor
                property real startX: 0
                property real startY: 0
                property real startW: 0
                property real startH: 0

                onPressed: function(mouse) {
                    startX = mouse.x + resizeHandle.x
                    startY = mouse.y + resizeHandle.y
                    startW = root.width
                    startH = root.height
                }

                onPositionChanged: function(mouse) {
                    if (!pressed) return
                    var dx = (mouse.x + resizeHandle.x) - startX
                    var dy = (mouse.y + resizeHandle.y) - startY
                    var newW = Math.max(root.minWidth, startW + dx)
                    var newH = Math.max(root.minHeight, startH + dy)
                    // Maintain square for joysticks and circle buttons
                    if (root.widgetType === "joystick" || root.widgetShape === "circle") {
                        var sz = Math.max(newW, newH)
                        root.width = snap(sz)
                        root.height = snap(sz)
                    } else {
                        root.width = snap(newW)
                        root.height = snap(newH)
                    }
                }

                onReleased: {
                    root.width = snap(root.width)
                    root.height = snap(root.height)
                    root.widgetResized(root.widgetId, root.width, root.height)
                }
            }
        }
    }

    // ==================== WIDGET CONTENT ====================
    // The actual interactive control rendered based on widgetType
    Loader {
        id: contentLoader
        anchors.fill: parent
        sourceComponent: {
            if (root.widgetType === "joystick") return joystickContent
            if (root.widgetType === "button") return buttonContent
            if (root.widgetType === "slider") return sliderContent
            if (root.widgetType === "dpad") return dpadContent
            if (root.widgetType === "wheel") return wheelContent
            return null
        }
        // Block mouse events to content when in edit mode (so drag works)
        enabled: !root.editMode
    }

    // ==================== JOYSTICK ====================
    Component {
        id: joystickContent

        Item {
            id: joyRoot
            property real xValue: 0
            property real yValue: 0
            property bool mouseLocked: false  // Triple-click lock state
            onMouseLockedChanged: {
                root.joystickLocked = mouseLocked
                root.mouseLockChanged(root.widgetId, mouseLocked)
                // Return to center when unlocking
                if (!mouseLocked) {
                    xValue = 0
                    yValue = 0
                    _sendJoystickValues(0, 0)
                }
            }

            readonly property real borderWidth: 2
            readonly property real joyRadius: Math.max(1, Math.min(width, height) / 2 - borderWidth / 2)
            readonly property real thumbRadius: Math.min(width, height) * 0.18 * 0.5
            readonly property real effectiveRadius: Math.max(1, joyRadius - thumbRadius)
            readonly property real centerX: width / 2
            readonly property real centerY: height / 2

            property real _pressX: 0
            property real _pressY: 0
            property real _startXValue: 0
            property real _startYValue: 0

            // Triple-click detection
            property int _clickCount: 0
            property real _lastClickTime: 0

            function _checkTripleClick() {
                if (!root.tripleClickEnabled) return
                var now = Date.now()
                if (now - _lastClickTime < 400) {
                    _clickCount++
                } else {
                    _clickCount = 1
                }
                _lastClickTime = now
                if (_clickCount >= 3) {
                    _clickCount = 0
                    mouseLocked = !mouseLocked
                    // Signal emitted via onMouseLockedChanged
                }
            }

            // Macro mode state
            property string _currentZone: "none"
            property var _activeButtons: []
            property bool _turboState: false

            // Turbo timer for repeat button presses
            Timer {
                id: turboTimer
                interval: 100
                repeat: true
                running: false
                onTriggered: {
                    if (!controller || !root.macroMode) return
                    var zoneCfg = joyRoot._getZoneConfig(joyRoot._currentZone)
                    if (zoneCfg && zoneCfg.action === "turbo" && zoneCfg.buttons) {
                        joyRoot._turboState = !joyRoot._turboState
                        for (var i = 0; i < zoneCfg.buttons.length; i++) {
                            controller.setButton(zoneCfg.buttons[i], joyRoot._turboState)
                        }
                    }
                }
            }

            function _getZoneConfig(zone) {
                if (!root.macroConfig || !root.macroConfig.zones) return null
                return root.macroConfig.zones[zone] || null
            }

            function _detectZone(nx, ny) {
                var dz = (root.macroConfig.deadzone_percent || 30) / 100.0
                var mag = Math.sqrt(nx * nx + ny * ny)

                // Center zone (within deadzone)
                if (mag < dz) return "center"

                // Get angle in degrees (-180 to 180, 0 = right/east)
                var angle = Math.atan2(-ny, nx) * 180 / Math.PI

                // 8-way mode: 45-degree sectors
                var is8way = root.macroConfig.diagonal_mode !== "4-way"

                if (is8way) {
                    // 8 zones, each 45 degrees
                    if (angle >= -22.5 && angle < 22.5) return "east"
                    if (angle >= 22.5 && angle < 67.5) return "northeast"
                    if (angle >= 67.5 && angle < 112.5) return "north"
                    if (angle >= 112.5 && angle < 157.5) return "northwest"
                    if (angle >= 157.5 || angle < -157.5) return "west"
                    if (angle >= -157.5 && angle < -112.5) return "southwest"
                    if (angle >= -112.5 && angle < -67.5) return "south"
                    if (angle >= -67.5 && angle < -22.5) return "southeast"
                } else {
                    // 4-way mode: 90-degree sectors (cardinal only)
                    if (angle >= -45 && angle < 45) return "east"
                    if (angle >= 45 && angle < 135) return "north"
                    if (angle >= 135 || angle < -135) return "west"
                    if (angle >= -135 && angle < -45) return "south"
                }

                return "none"
            }

            function _executeMacroAction(zone, active) {
                if (!controller) return
                var cfg = _getZoneConfig(zone)
                if (!cfg || cfg.action === "none") return

                if (cfg.action === "button" || cfg.action === "multi_button") {
                    // Press or release button(s)
                    if (cfg.buttons) {
                        for (var i = 0; i < cfg.buttons.length; i++) {
                            controller.setButton(cfg.buttons[i], active)
                        }
                    }
                } else if (cfg.action === "axis") {
                    // Set axis to configured value when active, 0 when inactive
                    var axis = cfg.axis || "x"
                    var value = active ? (cfg.axis_value !== undefined ? cfg.axis_value : 1.0) : 0.0
                    controller.setAxis(axis, value)
                } else if (cfg.action === "axis_button") {
                    // Fire axis AND button(s) simultaneously
                    var abAxis = cfg.axis || "x"
                    var abValue = active ? (cfg.axis_value !== undefined ? cfg.axis_value : 1.0) : 0.0
                    controller.setAxis(abAxis, abValue)
                    if (cfg.buttons) {
                        for (var k = 0; k < cfg.buttons.length; k++) {
                            controller.setButton(cfg.buttons[k], active)
                        }
                    }
                } else if (cfg.action === "turbo") {
                    if (active) {
                        // Start turbo
                        var hz = cfg.turbo_hz || 10
                        turboTimer.interval = Math.max(16, Math.floor(1000 / (hz * 2)))
                        turboTimer.start()
                    } else {
                        // Stop turbo and release button
                        turboTimer.stop()
                        joyRoot._turboState = false
                        if (cfg.buttons) {
                            for (var j = 0; j < cfg.buttons.length; j++) {
                                controller.setButton(cfg.buttons[j], false)
                            }
                        }
                    }
                }
            }

            function _updateMacroZone(nx, ny) {
                var newZone = _detectZone(nx, ny)
                if (newZone !== _currentZone) {
                    // Deactivate old zone
                    if (_currentZone !== "none") {
                        _executeMacroAction(_currentZone, false)
                    }
                    // Activate new zone
                    _currentZone = newZone
                    if (newZone !== "none") {
                        _executeMacroAction(newZone, true)
                    }
                }
            }

            function _sendJoystickValues(nx, ny) {
                if (!controller) return

                // Macro mode: convert joystick position to zone actions
                if (root.macroMode) {
                    _updateMacroZone(nx, ny)
                    return
                }

                // Normal mode: raw screen-space deflection (right and down
                // positive). The bridge shapes it, flips Y, applies the
                // widget's inversion, and routes it to the mapped axes.
                controller.setStickInput(root.widgetId, nx, ny)
            }

            // Mouse pixels from the press point to full deflection. The thumb
            // is still drawn at effectiveRadius, so a larger travel means the
            // thumb moves less than the cursor: more precision per pixel.
            readonly property real travelRadius: root.travelPx > 0 ? root.travelPx : effectiveRadius

            // Base circle
            Rectangle {
                width: joyRoot.joyRadius * 2
                height: width
                anchors.centerIn: parent
                radius: width / 2
                color: "#1e1e1e"
                border.color: root.macroMode ? "#e040fb" : (joyRoot.mouseLocked ? "#ff6a00" : "#3d3d3d")
                border.width: joyRoot.borderWidth
            }

            // Macro mode zone overlay
            Canvas {
                id: macroZoneCanvas
                anchors.centerIn: parent
                width: joyRoot.joyRadius * 2
                height: width
                visible: root.macroMode
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.clearRect(0, 0, width, height)
                    var cx = width / 2
                    var cy = height / 2
                    var r = width / 2 - 2
                    var dz = (root.macroConfig.deadzone_percent || 30) / 100.0

                    // Draw zone divider lines
                    ctx.strokeStyle = "#e040fb44"
                    ctx.lineWidth = 1
                    var is8way = root.macroConfig.diagonal_mode !== "4-way"
                    var numZones = is8way ? 8 : 4
                    var angleStep = 360 / numZones
                    var startOffset = is8way ? 22.5 : 45

                    for (var i = 0; i < numZones; i++) {
                        var angle = (i * angleStep + startOffset) * Math.PI / 180
                        ctx.beginPath()
                        ctx.moveTo(cx + r * dz * Math.cos(angle), cy - r * dz * Math.sin(angle))
                        ctx.lineTo(cx + r * Math.cos(angle), cy - r * Math.sin(angle))
                        ctx.stroke()
                    }

                    // Draw deadzone circle
                    ctx.strokeStyle = "#e040fb66"
                    ctx.beginPath()
                    ctx.arc(cx, cy, r * dz, 0, Math.PI * 2)
                    ctx.stroke()
                }
            }

            // Macro mode indicator
            Text {
                anchors.top: parent.top
                anchors.topMargin: 4
                anchors.horizontalCenter: parent.horizontalCenter
                text: "MACRO"
                color: "#e040fb"
                font.pixelSize: 9
                font.bold: true
                visible: root.macroMode && !joyRoot.mouseLocked
            }

            // Current zone indicator (when active)
            Text {
                anchors.centerIn: parent
                text: {
                    var labels = {
                        "north": "↑", "northeast": "↗", "east": "→", "southeast": "↘",
                        "south": "↓", "southwest": "↙", "west": "←", "northwest": "↖",
                        "center": "●", "none": ""
                    }
                    return labels[joyRoot._currentZone] || ""
                }
                color: "#e040fb"
                font.pixelSize: Math.min(joyRoot.width, joyRoot.height) * 0.25
                font.bold: true
                visible: root.macroMode && joyRoot._currentZone !== "none"
                opacity: 0.7
            }

            // Thumb
            Rectangle {
                id: joyThumb
                width: Math.min(joyRoot.width, joyRoot.height) * 0.18
                height: width
                radius: width / 2
                color: joyRoot.mouseLocked ? "#d45500" : "#2e6bd1"
                border.color: joyRoot.mouseLocked ? "#ff8833" : "#6aa3ff"
                x: joyRoot.centerX + joyRoot.xValue * joyRoot.effectiveRadius - width / 2
                y: joyRoot.centerY + joyRoot.yValue * joyRoot.effectiveRadius - height / 2

                Behavior on x { NumberAnimation { duration: 280; easing.type: Easing.OutCubic } }
                Behavior on y { NumberAnimation { duration: 280; easing.type: Easing.OutCubic } }
            }

            // Lock indicator
            Text {
                anchors.top: parent.top
                anchors.topMargin: 4
                anchors.horizontalCenter: parent.horizontalCenter
                text: "LOCKED"
                color: "#ff6a00"
                font.pixelSize: 10
                font.bold: true
                visible: joyRoot.mouseLocked
            }

            // Label
            Text {
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 4
                anchors.horizontalCenter: parent.horizontalCenter
                text: root.widgetLabel
                color: "#666"
                font.pixelSize: 11
                visible: root.widgetLabel !== ""
            }

            // Normal (unlocked) interaction — locked mode handled by parent overlay
            MouseArea {
                anchors.fill: parent
                enabled: !joyRoot.mouseLocked

                onPressed: function(mouse) {
                    joyRoot._checkTripleClick()
                    joyRoot._pressX = mouse.x
                    joyRoot._pressY = mouse.y
                    joyRoot._startXValue = joyRoot.xValue
                    joyRoot._startYValue = joyRoot.yValue
                }
                onPositionChanged: function(mouse) {
                    if (!pressed) return
                    var dx = mouse.x - joyRoot._pressX
                    var dy = mouse.y - joyRoot._pressY
                    var nx = joyRoot._startXValue + (dx / joyRoot.travelRadius)
                    var ny = joyRoot._startYValue + (dy / joyRoot.travelRadius)
                    var mag2 = nx * nx + ny * ny
                    if (mag2 > 1.0) {
                        var mag = Math.sqrt(mag2)
                        nx /= mag
                        ny /= mag
                    }
                    joyRoot.xValue = nx
                    joyRoot.yValue = ny
                    joyRoot._sendJoystickValues(nx, ny)
                }
                onReleased: {
                    joyRoot.xValue = 0
                    joyRoot.yValue = 0
                    joyRoot._sendJoystickValues(0, 0)
                }
            }
        }
    }

    // ==================== BUTTON ====================
    Component {
        id: buttonContent

        Rectangle {
            id: btnRect
            property bool isPressed: false
            property bool isToggled: false

            radius: root.widgetShape === "circle" ? width / 2 : (root.widgetShape === "square" ? 4 : 8)
            color: {
                var base = root.widgetColor || "#333"
                if (isPressed || isToggled) return Qt.lighter(base, 1.4)
                if (btnMouseArea.containsMouse) return Qt.lighter(base, 1.2)
                return base
            }
            border.color: {
                if (isPressed || isToggled) return Qt.lighter(root.widgetColor || "#555", 1.6)
                return Qt.lighter(root.widgetColor || "#555", 1.3)
            }
            border.width: 3

            Text {
                anchors.centerIn: parent
                text: root.widgetLabel
                color: "white"
                font.pixelSize: Math.min(parent.width, parent.height) * 0.35
                font.bold: true
            }

            // Modifier badge: this button holds an aim modifier instead of a gamepad button
            Text {
                anchors.top: parent.top
                anchors.topMargin: 3
                anchors.horizontalCenter: parent.horizontalCenter
                text: root.modifier === "precision" ? "PRECISION" : ""
                color: "#ffd166"
                font.pixelSize: 8
                font.bold: true
                visible: root.modifier !== "none"
            }

            MouseArea {
                id: btnMouseArea
                anchors.fill: parent
                hoverEnabled: true

                // A modifier button never touches a gamepad button; it holds
                // or latches a bridge modifier (momentary or toggle like any
                // other button).
                function _send(active) {
                    if (!controller) return
                    if (root.modifier !== "none") controller.setModifier(root.modifier, active)
                    else controller.setButton(root.buttonId, active)
                }

                onPressed: {
                    // Use per-widget toggleMode first, fall back to global config
                    var isToggle = root.toggleMode || (controller && root.modifier === "none" ? controller.isButtonToggle(root.buttonId) : false)
                    if (isToggle) {
                        btnRect.isToggled = !btnRect.isToggled
                        _send(btnRect.isToggled)
                    } else {
                        btnRect.isPressed = true
                        _send(true)
                    }
                }
                onReleased: {
                    var isToggle = root.toggleMode || (controller && root.modifier === "none" ? controller.isButtonToggle(root.buttonId) : false)
                    if (!isToggle) {
                        btnRect.isPressed = false
                        _send(false)
                    }
                }
            }
        }
    }

    // ==================== SLIDER ====================
    Component {
        id: sliderContent

        Rectangle {
            id: sliderRect
            color: "#252525"
            border.color: "#444"
            border.width: 2
            radius: 10

            readonly property bool isVertical: root.orientation === "vertical"
            readonly property bool isCenter: root.snapMode === "center"

            // Label
            Text {
                id: sliderLabel
                anchors.top: parent.top
                anchors.topMargin: 4
                anchors.horizontalCenter: parent.horizontalCenter
                text: root.widgetLabel
                color: "#aaa"
                font.pixelSize: 12
                font.bold: true
            }

            // Track
            Rectangle {
                id: sliderTrack
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 8
                anchors.horizontalCenter: parent.horizontalCenter
                width: parent.width - 16
                height: Math.max(20, parent.height - sliderLabel.height - 20)
                radius: 6
                color: "#1a1a1a"

                // Normal fill (left-to-right or bottom-to-top)
                // No anchors — explicit x/y/width/height avoids QML anchor conflicts
                Rectangle {
                    visible: !sliderRect.isCenter
                    x: 0
                    y: sliderRect.isVertical ? parent.height * (1 - sliderDragArea.value) : 0
                    width: sliderRect.isVertical ? parent.width : parent.width * sliderDragArea.value
                    height: sliderRect.isVertical ? parent.height * sliderDragArea.value : parent.height
                    radius: 6
                    color: "#107c10"
                }

                // Center fill (deviation from center)
                Rectangle {
                    visible: sliderRect.isCenter
                    x: sliderRect.isVertical ? 0 : (sliderDragArea.value >= 0.5 ? parent.width / 2 : parent.width * sliderDragArea.value)
                    y: sliderRect.isVertical ? (sliderDragArea.value >= 0.5 ? parent.height * (1 - sliderDragArea.value) : parent.height / 2) : 0
                    width: sliderRect.isVertical ? parent.width : Math.abs(sliderDragArea.value - 0.5) * parent.width
                    height: sliderRect.isVertical ? Math.abs(sliderDragArea.value - 0.5) * parent.height : parent.height
                    radius: 6
                    color: "#2e6bd1"
                }

                // Center line
                Rectangle {
                    visible: sliderRect.isCenter
                    x: sliderRect.isVertical ? 0 : parent.width / 2 - 1
                    y: sliderRect.isVertical ? parent.height / 2 - 1 : 0
                    width: sliderRect.isVertical ? parent.width : 2
                    height: sliderRect.isVertical ? 2 : parent.height
                    color: "#666"
                }

                MouseArea {
                    id: sliderDragArea
                    property real value: sliderRect.isCenter ? 0.5 : 0
                    property real _dragStartValue: 0
                    property real _dragStartMouse: 0
                    anchors.fill: parent

                    // Explicit snap-back animation — sends smoothed values to vJoy every frame
                    NumberAnimation {
                        id: snapBackAnim
                        target: sliderDragArea
                        property: "value"
                        duration: 300
                        easing.type: Easing.OutCubic
                    }

                    // Send axis values on every value change (drag AND animation frames)
                    onValueChanged: _sendSliderValue(value)

                    function _rawFromMouse(mouse) {
                        if (sliderRect.isVertical) {
                            return Math.max(0, Math.min(1, 1 - mouse.y / height))
                        } else {
                            return Math.max(0, Math.min(1, mouse.x / width))
                        }
                    }

                    function _mousePos(mouse) {
                        return sliderRect.isVertical ? mouse.y : mouse.x
                    }

                    function _trackLen() {
                        return sliderRect.isVertical ? height : width
                    }

                    onPositionChanged: function(mouse) {
                        snapBackAnim.stop()
                        if (root.clickMode === "relative") {
                            var delta = (_mousePos(mouse) - _dragStartMouse) / _trackLen()
                            if (sliderRect.isVertical) delta = -delta
                            value = Math.max(0, Math.min(1, _dragStartValue + delta))
                        } else {
                            value = _rawFromMouse(mouse)
                        }
                    }
                    onPressed: function(mouse) {
                        snapBackAnim.stop()
                        if (root.clickMode === "relative") {
                            _dragStartValue = value
                            _dragStartMouse = _mousePos(mouse)
                        } else {
                            value = _rawFromMouse(mouse)
                        }
                    }
                    onReleased: {
                        if (root.snapMode === "center") {
                            snapBackAnim.to = 0.5
                            snapBackAnim.start()
                        } else if (root.snapMode === "left") {
                            snapBackAnim.to = 0
                            snapBackAnim.start()
                        }
                        // "none": hold position
                    }

                    function _sendSliderValue(v) {
                        if (!controller) return
                        // Centre-sprung sliders are bipolar (-1..1); hold and
                        // return-to-zero sliders are unipolar (0..1). The bridge
                        // shapes each accordingly and routes to the mapped axis.
                        if (root.snapMode === "center") {
                            controller.setAxisInput(root.widgetId, (v - 0.5) * 2)
                        } else {
                            controller.setAxisInput(root.widgetId, v)
                        }
                    }
                }
            }
        }
    }

    // ==================== D-PAD ====================
    Component {
        id: dpadContent

        Item {
            id: dpadRoot
            // Size each button so 3 fit in both dimensions with gaps and label space
            readonly property real availH: height - 16  // reserve for label
            readonly property real btnSize: Math.floor((Math.min(width, availH) - 2 * gap) / 3)
            readonly property real gap: 2
            readonly property real cx: width / 2
            readonly property real cy: availH / 2

            function _pressDir(dir, pressed) {
                if (!controller) return
                var m = root.mapping || {}
                var btnId = m[dir] || 0
                if (btnId > 0) controller.setButton(btnId, pressed)
            }

            // Up
            Rectangle {
                x: dpadRoot.cx - dpadRoot.btnSize / 2
                y: dpadRoot.cy - dpadRoot.btnSize * 1.5 - dpadRoot.gap
                width: dpadRoot.btnSize; height: dpadRoot.btnSize
                radius: 4; color: dpadUp.pressed ? "#4a9eff" : "#333"
                border.color: "#555"; border.width: 1
                Text { anchors.centerIn: parent; text: "\u25B2"; color: "white"; font.pixelSize: dpadRoot.btnSize * 0.4 }
                MouseArea { id: dpadUp; anchors.fill: parent
                    onPressed: dpadRoot._pressDir("up", true)
                    onReleased: dpadRoot._pressDir("up", false)
                }
            }
            // Down
            Rectangle {
                x: dpadRoot.cx - dpadRoot.btnSize / 2
                y: dpadRoot.cy + dpadRoot.btnSize * 0.5 + dpadRoot.gap
                width: dpadRoot.btnSize; height: dpadRoot.btnSize
                radius: 4; color: dpadDown.pressed ? "#4a9eff" : "#333"
                border.color: "#555"; border.width: 1
                Text { anchors.centerIn: parent; text: "\u25BC"; color: "white"; font.pixelSize: dpadRoot.btnSize * 0.4 }
                MouseArea { id: dpadDown; anchors.fill: parent
                    onPressed: dpadRoot._pressDir("down", true)
                    onReleased: dpadRoot._pressDir("down", false)
                }
            }
            // Left
            Rectangle {
                x: dpadRoot.cx - dpadRoot.btnSize * 1.5 - dpadRoot.gap
                y: dpadRoot.cy - dpadRoot.btnSize / 2
                width: dpadRoot.btnSize; height: dpadRoot.btnSize
                radius: 4; color: dpadLeft.pressed ? "#4a9eff" : "#333"
                border.color: "#555"; border.width: 1
                Text { anchors.centerIn: parent; text: "\u25C0"; color: "white"; font.pixelSize: dpadRoot.btnSize * 0.4 }
                MouseArea { id: dpadLeft; anchors.fill: parent
                    onPressed: dpadRoot._pressDir("left", true)
                    onReleased: dpadRoot._pressDir("left", false)
                }
            }
            // Right
            Rectangle {
                x: dpadRoot.cx + dpadRoot.btnSize * 0.5 + dpadRoot.gap
                y: dpadRoot.cy - dpadRoot.btnSize / 2
                width: dpadRoot.btnSize; height: dpadRoot.btnSize
                radius: 4; color: dpadRight.pressed ? "#4a9eff" : "#333"
                border.color: "#555"; border.width: 1
                Text { anchors.centerIn: parent; text: "\u25B6"; color: "white"; font.pixelSize: dpadRoot.btnSize * 0.4 }
                MouseArea { id: dpadRight; anchors.fill: parent
                    onPressed: dpadRoot._pressDir("right", true)
                    onReleased: dpadRoot._pressDir("right", false)
                }
            }
            // Center cross
            Rectangle {
                x: dpadRoot.cx - dpadRoot.btnSize / 2; y: dpadRoot.cy - dpadRoot.btnSize / 2
                width: dpadRoot.btnSize; height: dpadRoot.btnSize
                radius: 4; color: "#222"; border.color: "#444"; border.width: 1
            }
            // Label
            Text {
                anchors.bottom: parent.bottom; anchors.bottomMargin: 2
                anchors.horizontalCenter: parent.horizontalCenter
                text: root.widgetLabel; color: "#666"; font.pixelSize: 10
                visible: root.widgetLabel !== ""
            }
        }
    }

    // ==================== STEERING WHEEL ====================
    Component {
        id: wheelContent

        Item {
            id: wheelRoot
            property real angle: 0  // -1 to +1

            readonly property real wheelRadius: Math.min(width, height) / 2 - 6
            readonly property real cx: width / 2
            readonly property real cy: height / 2

            // Outer ring
            Rectangle {
                anchors.centerIn: parent
                width: wheelRoot.wheelRadius * 2; height: width
                radius: width / 2
                color: "transparent"
                border.color: "#555"; border.width: 4
            }

            // Inner ring
            Rectangle {
                anchors.centerIn: parent
                width: wheelRoot.wheelRadius * 1.4; height: width
                radius: width / 2
                color: "transparent"
                border.color: "#333"; border.width: 2
            }

            // Rotation indicator (spoke)
            Rectangle {
                id: wheelSpoke
                width: 6; height: wheelRoot.wheelRadius * 0.8
                radius: 3
                color: "#2e6bd1"
                x: wheelRoot.cx - 3
                y: wheelRoot.cy - wheelRoot.wheelRadius * 0.8
                transformOrigin: Item.Bottom
                rotation: wheelRoot.angle * 135  // ±135 degrees visual range

                Behavior on rotation { NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
            }

            // Center hub
            Rectangle {
                anchors.centerIn: parent
                width: 16; height: 16; radius: 8
                color: "#444"; border.color: "#666"; border.width: 1
            }

            // Label
            Text {
                anchors.bottom: parent.bottom; anchors.bottomMargin: 2
                anchors.horizontalCenter: parent.horizontalCenter
                text: root.widgetLabel; color: "#666"; font.pixelSize: 10
                visible: root.widgetLabel !== ""
            }

            MouseArea {
                anchors.fill: parent
                property real _pressX: 0

                onPressed: function(mouse) { _pressX = mouse.x }
                onPositionChanged: function(mouse) {
                    if (!pressed) return
                    // Map horizontal drag to -1..+1
                    var raw = ((mouse.x / width) * 2 - 1)
                    wheelRoot.angle = Math.max(-1, Math.min(1, raw))
                    if (controller) controller.setAxisInput(root.widgetId, wheelRoot.angle)
                }
                onReleased: {
                    // Spring to center
                    wheelRoot.angle = 0
                    if (controller) controller.setAxisInput(root.widgetId, 0)
                }
            }
        }
    }
}
