import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

/*
 * AccountDialog.qml — Sign In / Account Management dialog
 *
 * Provides:
 *   - Email + password sign-in / sign-up
 *   - Google OAuth button
 *   - Facebook OAuth button
 *   - Account status display when signed in
 *   - Sign Out button
 *
 * Exposed context properties used:
 *   - controller (ControllerBridge)
 */

Dialog {
    id: accountDialog
    title: controller && controller.accountAuthenticated ? "Account" : "Sign In"
    modal: true
    width: Math.min(420, parent ? parent.width - 32 : 420)
    height: Math.min(contentColumn.implicitHeight + 80, parent ? parent.height - 32 : 600)
    anchors.centerIn: parent
    padding: 24

    header: Label {
        text: accountDialog.title
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

    // ---- State ----
    property bool isSignUp: false
    property bool isLoading: false
    property string errorMessage: ""

    contentItem: Flickable {
        id: accountScroll
        contentHeight: contentColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {}

    ColumnLayout {
        id: contentColumn
        width: accountScroll.width
        spacing: 16

        // ---- Header ----
        Label {
            text: controller && controller.accountAuthenticated
                ? "Welcome, " + controller.accountDisplayName
                  : (accountDialog.isSignUp ? "Create Account" : "Sign In to Nimbus")
            font.pixelSize: 18
            font.bold: true
            color: "#ffffff"
            Layout.alignment: Qt.AlignHCenter
        }

        // ---- Signed-In View ----
        ColumnLayout {
            visible: controller && controller.accountAuthenticated
            spacing: 12
            Layout.fillWidth: true

            // Avatar + email
            RowLayout {
                spacing: 12
                Layout.alignment: Qt.AlignHCenter

                Rectangle {
                    width: 48; height: 48; radius: 24
                    color: "#4a9eff"
                    Label {
                        anchors.centerIn: parent
                        text: controller ? controller.accountDisplayName.charAt(0).toUpperCase() : "?"
                        font.pixelSize: 20; font.bold: true; color: "#ffffff"
                    }
                }

                ColumnLayout {
                    spacing: 2
                    Label {
                        text: controller ? controller.accountDisplayName : ""
                        font.pixelSize: 14; color: "#ffffff"
                    }
                    Label {
                        text: controller ? controller.accountEmail : ""
                        font.pixelSize: 11; color: "#888888"
                    }
                    Label {
                        text: controller ? ("Tier: " + controller.accountTier.replace("_", " ").toUpperCase()) : ""
                        font.pixelSize: 11; color: "#4a9eff"
                    }
                }
            }

            // Sync button (Nimbus+ only)
            Button {
                text: "Sync Profiles"
                visible: controller && controller.accountPremium
                Layout.fillWidth: true
                Layout.preferredHeight: 36
                onClicked: controller.syncProfiles()
                background: Rectangle {
                    color: parent.hovered ? "#3a8eef" : "#4a9eff"
                    radius: 6
                }
                contentItem: Label {
                    text: parent.text; color: "#ffffff"
                    font.pixelSize: 13; horizontalAlignment: Text.AlignHCenter
                }
            }

            // Sign out
            Button {
                text: "Sign Out"
                Layout.fillWidth: true
                Layout.preferredHeight: 36
                onClicked: {
                    controller.logoutAccount()
                    accountDialog.close()
                }
                background: Rectangle {
                    color: parent.hovered ? "#552222" : "#3a1111"
                    radius: 6; border.color: "#aa4444"; border.width: 1
                }
                contentItem: Label {
                    text: parent.text; color: "#ff6666"
                    font.pixelSize: 13; horizontalAlignment: Text.AlignHCenter
                }
            }
        }

        // ---- Sign-In / Sign-Up View ----
        ColumnLayout {
            visible: !controller || !controller.accountAuthenticated
            spacing: 12
            Layout.fillWidth: true

            // OAuth buttons
            Button {
                id: googleBtn
                enabled: false
                ToolTip.text: "Browser sign-in is not available in this build"
                ToolTip.visible: hovered
                Layout.fillWidth: true
                Layout.preferredHeight: 42
                onClicked: {
                    accountDialog.isLoading = true
                    controller.loginWithProvider("google")
                }
                background: Rectangle {
                    color: googleBtn.hovered ? "#ffffff" : "#f5f5f5"
                    radius: 6
                }
                contentItem: RowLayout {
                    spacing: 10
                    Item { Layout.fillWidth: true }
                    Label { text: "G"; font.pixelSize: 16; font.bold: true; color: "#4285F4" }
                    Label { text: "Continue with Google"; font.pixelSize: 13; color: "#333333" }
                    Item { Layout.fillWidth: true }
                }
            }

            Button {
                id: facebookBtn
                enabled: false
                ToolTip.text: "Browser sign-in is not available in this build"
                ToolTip.visible: hovered
                Layout.fillWidth: true
                Layout.preferredHeight: 42
                onClicked: {
                    accountDialog.isLoading = true
                    controller.loginWithProvider("facebook")
                }
                background: Rectangle {
                    color: facebookBtn.hovered ? "#1565C0" : "#1877F2"
                    radius: 6
                }
                contentItem: RowLayout {
                    spacing: 10
                    Item { Layout.fillWidth: true }
                    Label { text: "f"; font.pixelSize: 18; font.bold: true; color: "#ffffff" }
                    Label { text: "Continue with Facebook"; font.pixelSize: 13; color: "#ffffff" }
                    Item { Layout.fillWidth: true }
                }
            }

            // Divider
            RowLayout {
                spacing: 8
                Layout.fillWidth: true
                Rectangle { Layout.fillWidth: true; height: 1; color: "#333333" }
                Label { text: "or"; color: "#666666"; font.pixelSize: 11 }
                Rectangle { Layout.fillWidth: true; height: 1; color: "#333333" }
            }

            // Email field
            TextField {
                id: emailField
                placeholderText: "Email address"
                Layout.fillWidth: true
                Layout.preferredHeight: 38
                color: "#ffffff"
                placeholderTextColor: "#666666"
                font.pixelSize: 13
                background: Rectangle {
                    color: "#2a2a2a"; radius: 6
                    border.color: emailField.activeFocus ? "#4a9eff" : "#444444"
                    border.width: 1
                }
            }

            // Password field
            TextField {
                id: passwordField
                placeholderText: "Password"
                echoMode: TextInput.Password
                Layout.fillWidth: true
                Layout.preferredHeight: 38
                color: "#ffffff"
                placeholderTextColor: "#666666"
                font.pixelSize: 13
                background: Rectangle {
                    color: "#2a2a2a"; radius: 6
                    border.color: passwordField.activeFocus ? "#4a9eff" : "#444444"
                    border.width: 1
                }
                Keys.onReturnPressed: submitEmail()
            }

            // Error message
            Label {
                visible: accountDialog.errorMessage !== ""
                text: accountDialog.errorMessage
                color: "#ff6666"
                font.pixelSize: 11
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            // Submit button
            Button {
                id: submitBtn
                text: accountDialog.isSignUp ? "Create Account" : "Sign In with Email"
                enabled: emailField.text.length > 0 && passwordField.text.length > 0
                         && !accountDialog.isLoading
                Layout.fillWidth: true
                Layout.preferredHeight: 42
                onClicked: submitEmail()
                background: Rectangle {
                    color: submitBtn.enabled
                           ? (submitBtn.hovered ? "#3a8eef" : "#4a9eff")
                           : "#333333"
                    radius: 6
                }
                contentItem: Label {
                    text: accountDialog.isLoading ? "Signing in..." : parent.text
                    color: submitBtn.enabled ? "#ffffff" : "#666666"
                    font.pixelSize: 13; horizontalAlignment: Text.AlignHCenter
                }
            }

            // Toggle sign-in / sign-up
            Label {
                text: accountDialog.isSignUp
                      ? "Already have an account? <a href='#' style='color:#4a9eff'>Sign In</a>"
                      : "Don't have an account? <a href='#' style='color:#4a9eff'>Sign Up</a>"
                textFormat: Text.RichText
                color: "#888888"
                font.pixelSize: 11
                Layout.alignment: Qt.AlignHCenter
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        accountDialog.isSignUp = !accountDialog.isSignUp
                        accountDialog.errorMessage = ""
                    }
                }
            }
        }

        // Close button
        Button {
            text: "Close"
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 100
            Layout.preferredHeight: 32
            onClicked: accountDialog.close()
            background: Rectangle {
                color: parent.hovered ? "#333333" : "#2a2a2a"
                radius: 6; border.color: "#444444"; border.width: 1
            }
            contentItem: Label {
                text: parent.text; color: "#cccccc"
                font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter
            }
        }
    }
    }

    function submitEmail() {
        if (emailField.text.length === 0 || passwordField.text.length === 0) return
        accountDialog.isLoading = true
        accountDialog.errorMessage = ""

        var success
        if (accountDialog.isSignUp) {
            success = controller.signupWithEmail(emailField.text, passwordField.text)
        } else {
            success = controller.loginWithEmail(emailField.text, passwordField.text)
        }

        accountDialog.isLoading = false
        if (success) {
            accountDialog.close()
        } else {
            accountDialog.errorMessage = accountDialog.isSignUp
                ? "Sign up failed. Please check your email and try again."
                : "Sign in failed. Please check your credentials."
        }
    }

    onOpened: {
        accountDialog.errorMessage = ""
        accountDialog.isLoading = false
        emailField.text = ""
        passwordField.text = ""
    }
}
