import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

/*
 * UpdateNotification.qml — Non-intrusive update ribbon
 *
 * Displays a thin ribbon at the top of the window when a new version is
 * available.  The user can click to download or dismiss.
 *
 * Exposed context properties used:
 *   - controller (ControllerBridge)
 */

Rectangle {
    id: updateRibbon
    width: parent.width
    height: visible ? 36 : 0
    visible: false
    color: "#1a3a5c"
    z: 999

    property string latestVersion: ""
    property string downloadUrl: ""
    property string releaseNotes: ""
    property bool isForceUpdate: false

    Behavior on height { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        spacing: 10

        // Icon
        Label {
            text: updateRibbon.isForceUpdate ? "\u26A0" : "\u2B06"
            font.pixelSize: 14
            color: updateRibbon.isForceUpdate ? "#ffcc00" : "#4a9eff"
        }

        // Message
        Label {
            text: updateRibbon.isForceUpdate
                  ? "Your version is no longer supported. Please update to continue."
                  : "Update available — v" + updateRibbon.latestVersion
            font.pixelSize: 12
            color: "#ffffff"
            Layout.fillWidth: true
            elide: Text.ElideRight
        }

        // Release notes tooltip
        Label {
            visible: updateRibbon.releaseNotes !== "" && !updateRibbon.isForceUpdate
            text: updateRibbon.releaseNotes
            font.pixelSize: 10
            color: "#aaaaaa"
            elide: Text.ElideRight
            Layout.maximumWidth: 200
        }

        // Download button
        Button {
            text: "Download"
            Layout.preferredWidth: 80
            Layout.preferredHeight: 26
            onClicked: controller.openDownloadPage()
            background: Rectangle {
                color: parent.hovered ? "#3a8eef" : "#4a9eff"
                radius: 4
            }
            contentItem: Label {
                text: parent.text; color: "#ffffff"
                font.pixelSize: 11; horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
        }

        // Dismiss button (not shown for force updates)
        Button {
            visible: !updateRibbon.isForceUpdate
            text: "\u2715"
            Layout.preferredWidth: 26
            Layout.preferredHeight: 26
            onClicked: {
                controller.dismissUpdate()
                updateRibbon.visible = false
            }
            background: Rectangle {
                color: parent.hovered ? "#333333" : "transparent"
                radius: 4
            }
            contentItem: Label {
                text: parent.text; color: "#888888"
                font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
        }
    }

    // Connect to updater signals
    Connections {
        target: controller

        function onUpdateAvailable(version, url, notes) {
            updateRibbon.latestVersion = version
            updateRibbon.downloadUrl = url
            updateRibbon.releaseNotes = notes
            updateRibbon.isForceUpdate = false
            updateRibbon.visible = true
        }

        function onForceUpdateRequired(minVersion, url) {
            updateRibbon.latestVersion = minVersion
            updateRibbon.downloadUrl = url
            updateRibbon.isForceUpdate = true
            updateRibbon.visible = true
        }
    }
}
