import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

/*
 * SettingsPrivacyDialog.qml — Telemetry & Privacy settings
 *
 * Provides:
 *   - Opt-in / opt-out toggles for crash reports and usage analytics
 *   - "What we collect" transparency section showing the exact event schema
 *   - "What we NEVER collect" list for user confidence
 *   - Link to delete all collected data
 *
 * Exposed context properties used:
 *   - controller (ControllerBridge)
 */

Dialog {
    id: privacyDialog
    title: "Privacy & Telemetry"
    modal: true
    width: Math.min(480, parent ? parent.width - 32 : 480)
    height: Math.min(contentCol.implicitHeight + 80, parent ? parent.height - 32 : 600)
    anchors.centerIn: parent
    padding: 24

    header: Label {
        text: privacyDialog.title
        color: "#dddddd"
        font.pixelSize: 16
        padding: 16
        background: Rectangle { color: "#252525" }
    }

    background: Rectangle {
        color: "#1a1a1a"
        border.color: "#4a9eff"
        border.width: 1
        radius: 10
    }

    contentItem: Flickable {
        id: privacyScroll
        contentHeight: contentCol.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {}

    ColumnLayout {
        id: contentCol
        width: privacyScroll.width
        spacing: 16

        // ---- Header ----
        Label {
            text: "Privacy & Data Collection"
            font.pixelSize: 18
            font.bold: true
            color: "#ffffff"
            Layout.alignment: Qt.AlignHCenter
        }

        Label {
            text: "Nimbus Adaptive Controller respects your privacy. All data collection is opt-in, anonymous, and can be turned off at any time."
            font.pixelSize: 12
            color: "#aaaaaa"
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: "#333333" }

        // ---- Crash Reports Toggle ----
        RowLayout {
            spacing: 12
            Layout.fillWidth: true

            ColumnLayout {
                spacing: 2
                Layout.fillWidth: true
                Label {
                    text: "Send crash reports"
                    font.pixelSize: 14
                    color: "#ffffff"
                }
                Label {
                    text: "Helps fix bugs faster. Includes: exception type, stack trace hash, app version, OS version. Never includes controller inputs or personal information."
                    font.pixelSize: 11
                    color: "#888888"
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    Layout.maximumWidth: 340
                }
            }

            Switch {
                id: crashSwitch
                objectName: "crashReportsSwitch"
                checked: controller ? controller.crashReportsEnabled : false
                onToggled: {
                    if (controller) controller.setCrashReportsEnabled(checked)
                }
            }
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: "#2a2a2a" }

        // ---- Analytics Toggle ----
        RowLayout {
            spacing: 12
            Layout.fillWidth: true

            ColumnLayout {
                spacing: 2
                Layout.fillWidth: true
                Label {
                    text: "Send usage analytics"
                    font.pixelSize: 14
                    color: "#ffffff"
                }
                Label {
                    text: "Helps us understand which features and games are most used. Fully anonymous — no personal information, no controller inputs."
                    font.pixelSize: 11
                    color: "#888888"
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    Layout.maximumWidth: 340
                }
            }

            Switch {
                id: analyticsSwitch
                objectName: "analyticsSwitch"
                checked: controller ? controller.analyticsEnabled : false
                onToggled: {
                    if (controller) controller.setAnalyticsEnabled(checked)
                }
            }
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: "#333333" }

        // ---- What We Collect (expandable) ----
        ColumnLayout {
            spacing: 8
            Layout.fillWidth: true

            Label {
                text: schemaVisible ? "▼ What we collect" : "▶ What we collect"
                font.pixelSize: 13
                font.bold: true
                color: "#4a9eff"
                property bool schemaVisible: false
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: parent.schemaVisible = !parent.schemaVisible
                }
                id: schemaToggle
            }

            property bool schemaVisible: schemaToggle.schemaVisible

            ColumnLayout {
                visible: parent.schemaVisible
                spacing: 6
                Layout.fillWidth: true
                Layout.leftMargin: 12

                Repeater {
                    model: [
                        { title: "Session Start", fields: "App version, OS version, output mode (vJoy/ViGEm), driver availability" },
                        { title: "Profile Switch", fields: "Profile ID hash (SHA-256), layout type, widget count, output mode" },
                        { title: "Widget Added", fields: "Widget type (joystick/button/slider), output mode" },
                        { title: "Game Mode", fields: "Activation method, game detected (yes/no), game title hash (SHA-256)" },
                        { title: "Crash", fields: "Exception type, module, function, line number, stack trace hash, app version" }
                    ]
                    delegate: ColumnLayout {
                        spacing: 1
                        Layout.fillWidth: true
                        Label {
                            text: modelData.title
                            font.pixelSize: 12; font.bold: true; color: "#cccccc"
                        }
                        Label {
                            text: modelData.fields
                            font.pixelSize: 11; color: "#888888"
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }
                }
            }
        }

        // ---- What We NEVER Collect ----
        ColumnLayout {
            spacing: 8
            Layout.fillWidth: true

            Label {
                text: neverVisible ? "▼ What we NEVER collect" : "▶ What we NEVER collect"
                font.pixelSize: 13
                font.bold: true
                color: "#ff6666"
                property bool neverVisible: false
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: parent.neverVisible = !parent.neverVisible
                }
                id: neverToggle
            }

            property bool neverVisible: neverToggle.neverVisible

            ColumnLayout {
                visible: parent.neverVisible
                spacing: 4
                Layout.fillWidth: true
                Layout.leftMargin: 12

                Repeater {
                    model: [
                        "Button IDs, axis values, or any actual controller input",
                        "Profile names or descriptions",
                        "Window titles in plaintext (hash only)",
                        "File system paths",
                        "Content of macros or custom key bindings",
                        "Email addresses, names, or IP addresses"
                    ]
                    delegate: Label {
                        text: "✕  " + modelData
                        font.pixelSize: 11
                        color: "#aa6666"
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                }
            }
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: "#333333" }

        // ---- Close ----
        Button {
            text: "Done"
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 120
            Layout.preferredHeight: 36
            onClicked: privacyDialog.close()
            background: Rectangle {
                color: parent.hovered ? "#3a8eef" : "#4a9eff"
                radius: 6
            }
            contentItem: Label {
                text: parent.text; color: "#ffffff"
                font.pixelSize: 13; horizontalAlignment: Text.AlignHCenter
            }
        }
    }
    }
}
