import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.components 3.0 as PlasmaComponents
import org.kde.kirigami as Kirigami
import org.kde.plasma.workspace.dbus as DBus

PlasmoidItem {
    id: root

    preferredRepresentation: compactRepresentation
    Plasmoid.status: PlasmaCore.Types.ActiveStatus
    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground

    property string outputText: "就绪"
    property bool isListening: false
    property bool isProcessing: false
    property int selectedMode: 0
    property bool backendReady: false

    readonly property string robotIcon: Qt.resolvedUrl("../icons/robot.svg")
    readonly property string robotAvatar: Qt.resolvedUrl("../icons/robot.svg")

    function callBackend(method, args, callback) {
        var msg = {
            "service": "org.kde.plasma.llm-assistant",
            "path": "/llm_assistant",
            "iface": "org.kde.plasma.llm_assistant",
            "member": method
        }
        if (args && args.length > 0) msg.arguments = args
        var pending = DBus.SessionBus.asyncCall(msg)
        pending.finished.connect(function() {
            console.log("DBus " + method + " returned:", pending.value)
            if (callback) callback(pending.value)
            pending.destroy()
        })
        pending.error.connect(function(err) {
            console.log("DBus " + method + " error:", err)
        })
    }

    compactRepresentation: Item {
        width: 48
        height: 48

        Image {
            id: robotImg
            anchors.centerIn: parent
            width: 40
            height: 40
            source: root.robotIcon
            fillMode: Image.PreserveAspectFit
            smooth: true
            opacity: 0.85

            Behavior on opacity { NumberAnimation { duration: 150 } }
        }

        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            hoverEnabled: true
            onEntered: robotImg.opacity = 1.0
            onExited: robotImg.opacity = 0.85
            onClicked: root.expanded = true
        }
    }

    fullRepresentation: ColumnLayout {
        spacing: 0
        Layout.minimumWidth: 320
        Layout.minimumHeight: 420
        Layout.preferredWidth: 360
        Layout.preferredHeight: 480
        Layout.maximumWidth: 400

        MouseArea { anchors.fill: parent; acceptedButtons: Qt.NoButton }

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: 10
            spacing: 8

            Rectangle {
                width: 32; height: 32; radius: 10
                color: "#1e66f5"
                Image {
                    anchors.centerIn: parent
                    width: 22; height: 22
                    source: root.robotAvatar
                    fillMode: Image.PreserveAspectFit
                    smooth: true
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0
                PlasmaComponents.Label {
                    text: "AI 助手"
                    font.pixelSize: 13
                    font.bold: true
                    color: Kirigami.Theme.textColor
                }
                PlasmaComponents.Label {
                    text: root.backendReady ? "已连接" : "连接中..."
                    font.pixelSize: 9
                    color: root.backendReady ? Qt.rgba(0.25, 0.63, 0.17, 1) : Kirigami.Theme.disabledTextColor
                }
            }

            PlasmaComponents.ComboBox {
                model: ["问答", "命令"]
                currentIndex: root.selectedMode
                onCurrentIndexChanged: root.selectedMode = currentIndex
                Layout.preferredWidth: 72
                Layout.preferredHeight: 26
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.leftMargin: 10; Layout.rightMargin: 10
            Layout.preferredHeight: 1
            color: Qt.rgba(0, 0, 0, 0.08)
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: 10
            color: Qt.rgba(0, 0, 0, 0.03)
            radius: 8
            border.color: Qt.rgba(0, 0, 0, 0.08)
            border.width: 1

            PlasmaComponents.ScrollView {
                anchors.fill: parent
                anchors.margins: 8
                clip: true
                PlasmaComponents.TextArea {
                    id: outputArea
                    text: root.outputText
                    readOnly: true
                    wrapMode: Text.Wrap
                    color: Kirigami.Theme.textColor
                    font.pixelSize: 12
                    background: null
                    textFormat: Text.MarkdownText
                    onTextChanged: cursorPosition = text.length
                }
            }
        }

        PlasmaComponents.Label {
            text: root.isListening ? "🎤 正在听..." : root.isProcessing ? "⏳ 处理中..." : ""
            font.pixelSize: 10
            color: Qt.rgba(0.12, 0.4, 0.96, 1)
            visible: root.isListening || root.isProcessing
            Layout.alignment: Qt.AlignHCenter
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: 10; Layout.topMargin: 0
            spacing: 6

            Rectangle {
                Layout.fillWidth: true
                height: 34; radius: 8
                color: Qt.rgba(0, 0, 0, 0.03)
                border.color: inputField.activeFocus ? Qt.rgba(0.12, 0.4, 0.96, 1) : Qt.rgba(0, 0, 0, 0.08)
                border.width: 1
                PlasmaComponents.TextField {
                    id: inputField
                    anchors.fill: parent; anchors.margins: 4
                    placeholderText: "输入问题或命令..."
                    color: Kirigami.Theme.textColor
                    font.pixelSize: 12
                    background: null
                    placeholderTextColor: Kirigami.Theme.disabledTextColor
                    onAccepted: {
                        if (text.trim().length > 0) { root.sendQuery(text.trim()); text = "" }
                    }
                }
            }

            PlasmaComponents.ToolButton {
                icon.name: root.isListening ? "media-record-stop" : "audio-input-microphone"
                onClicked: { if (root.isListening) root.stopListening(); else root.startListening() }
                background: Rectangle { radius: 8; color: root.isListening ? Qt.rgba(0.82, 0.06, 0.22, 1) : Qt.rgba(0.25, 0.63, 0.17, 1) }
                contentItem: Kirigami.Icon { source: parent.icon.name; width: 18; height: 18; color: "white" }
            }

            PlasmaComponents.ToolButton {
                icon.name: "document-send"
                enabled: inputField.text.trim().length > 0 && !root.isProcessing
                onClicked: { root.sendQuery(inputField.text.trim()); inputField.text = "" }
                background: Rectangle { radius: 8; color: parent.enabled ? Qt.rgba(0.12, 0.4, 0.96, 1) : Qt.rgba(0, 0, 0, 0.05) }
                contentItem: Kirigami.Icon { source: parent.icon.name; width: 18; height: 18; color: parent.enabled ? "white" : Kirigami.Theme.disabledTextColor }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: 10; Layout.topMargin: 0
            spacing: 4
            Repeater {
                model: [
                    { icon: "audio-volume-high", cmd: "volume_up" },
                    { icon: "audio-volume-low", cmd: "volume_down" },
                    { icon: "weather-clear", cmd: "brightness_up" },
                    { icon: "weather-clear-night", cmd: "brightness_down" }
                ]
                PlasmaComponents.ToolButton {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 28
                    icon.name: modelData.icon
                    onClicked: root.sendCommand(modelData.cmd)
                    contentItem: Kirigami.Icon { source: modelData.icon; width: 14; height: 14; color: Kirigami.Theme.textColor }
                    background: Rectangle { radius: 6; color: parent.hovered ? Qt.rgba(0, 0, 0, 0.05) : "transparent"; border.color: Qt.rgba(0, 0, 0, 0.08); border.width: 1 }
                }
            }
        }
    }

    function sendQuery(text) {
        root.isProcessing = true
        root.outputText = "思考中...\n\n" + text
        callBackend("processQuery", [text, root.selectedMode === 1], function(r) { root.outputText = r; root.isProcessing = false })
    }
    function sendCommand(command) {
        root.isProcessing = true
        root.outputText = "执行: " + command
        callBackend("executeCommand", [command], function(r) { root.outputText = r; root.isProcessing = false })
    }
    function startListening() {
        root.isListening = true
        root.outputText = "🎤 正在听，请说话..."
        callBackend("startListening", [], function(r) {
            root.outputText = r
        })
    }
    function stopListening() {
        root.isListening = false
        callBackend("stopListening", [], function(r) { root.outputText = r })
    }
    Component.onCompleted: {
        callBackend("health", [], function() { root.backendReady = true })
    }

    Timer {
        id: stateTimer
        interval: 1000
        running: root.isListening
        repeat: true
        onTriggered: {
            callBackend("getState", [], function(r) {
                if (r === "idle") {
                    root.isListening = false
                    stateTimer.stop()
                }
            })
        }
    }
}
