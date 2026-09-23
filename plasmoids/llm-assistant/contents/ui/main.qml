import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.kirigami as Kirigami
import org.kde.plasma.workspace.dbus as DBus

PlasmoidItem {
    id: root
    preferredRepresentation: compactRepresentation
    Plasmoid.status: PlasmaCore.Types.ActiveStatus
    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground
    Plasmoid.icon: Qt.resolvedUrl("assistant.svg").toString()
    toolTipMainText: "小希 · 本地助手"
    toolTipSubText: "点击说话，或输入电脑控制指令"

    property bool backendReady: false
    property bool isListening: false
    property bool isProcessing: false
    property bool historyExpanded: false
    property bool pollPending: false
    property int voiceEpoch: 0
    property string statusText: "连接中"
    property string feedback: "点击麦克风开始 · 常用电脑控制无需大模型"
    property string draft: ""
    readonly property bool busy: isListening || isProcessing
    readonly property color ink: "#183e36"
    readonly property color muted: "#738780"
    readonly property color accent: "#226b55"

    ListModel { id: conversation }

    component SoftButton: Controls.Button {
        id: control
        property bool primary: false
        property bool quiet: false
        implicitHeight: 34
        leftPadding: 12
        rightPadding: 12
        font.pixelSize: 12
        contentItem: Text {
            text: control.text
            font: control.font
            color: !control.enabled ? "#a5b5ac" : control.primary ? "#ffffff" : "#547266"
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: 9
            color: control.primary ? (control.enabled ? (control.down ? "#144833" : control.hovered ? "#18573f" : "#226b55") : "#d9e5dc")
                : control.hovered ? "#e3eee7" : control.quiet ? "transparent" : "#edf3ef"
            border.width: control.primary || control.quiet ? 0 : 1
            border.color: control.activeFocus ? "#226b55" : "#e0e9e3"
        }
    }

    function addMessage(speaker, content) {
        conversation.append({"speaker": speaker, "body": String(content)})
        while (conversation.count > 60) conversation.remove(0)
    }

    function callBackend(method, args, callback, errorCallback) {
        var pending = DBus.SessionBus.asyncCall({
            "service": "org.kde.plasma.llm-assistant",
            "path": "/llm_assistant",
            "iface": "org.kde.plasma.llm_assistant",
            "member": method,
            "arguments": args || []
        })
        pending.finished.connect(function() {
            if (pending.isError) {
                root.backendReady = false
                root.statusText = "未连接"
                root.feedback = "助手暂时无法连接，点击下方“重新连接”重试。"
                if (errorCallback) errorCallback()
            } else {
                root.backendReady = true
                if (callback) callback(pending.value)
            }
            pending.destroy()
        })
    }

    function connectBackend() {
        callBackend("health", [], function() {
            if (!root.busy) {
                root.statusText = "待命"
                root.feedback = "点击麦克风开始 · 常用电脑控制无需大模型"
            }
        })
    }

    function sendQuery(text) {
        text = text.trim()
        if (!text || root.busy) return false
        root.isProcessing = true
        root.statusText = "处理中"
        root.feedback = "正在处理你的请求…"
        addMessage("user", text)
        callBackend("processQuery", [text, false], function(result) {
            root.isProcessing = false
            var answer = String(result || "没有收到回复，请重试。")
            addMessage("assistant", answer)
            root.feedback = answer
            root.statusText = /失败|错误|未运行|拒绝|异常/.test(answer) ? "未完成" : "完成"
        }, function() { root.isProcessing = false })
        return true
    }

    function startListening() {
        if (root.busy) return
        root.voiceEpoch += 1
        var epoch = root.voiceEpoch
        root.isListening = true
        root.statusText = "语音中"
        root.feedback = "请说话，常用指令识别后执行 · 再点一次取消"
        callBackend("startListening", [], function(result) {
            if (epoch !== root.voiceEpoch) return
            if (/失败|不存在/.test(String(result))) {
                root.isListening = false
                root.statusText = "未完成"
                root.feedback = String(result)
            }
        }, function() { root.isListening = false })
    }

    function stopListening() {
        root.voiceEpoch += 1
        root.isListening = false
        root.statusText = "已取消"
        callBackend("stopListening", [], function(result) { root.feedback = String(result) })
    }

    function finishVoice(result) {
        var message = String(result || "没有识别到语音，请重试。")
        var split = message.indexOf("\n\n")
        if (message.indexOf("听到：") === 0 && split >= 0) {
            var heard = message.substring(3, split)
            var answer = message.substring(split + 2)
            addMessage("user", heard)
            addMessage("assistant", answer)
            root.feedback = answer
            if (answer.indexOf("请确认识别内容") >= 0) root.draft = heard
        } else {
            root.feedback = message
            addMessage("assistant", message)
        }
        root.statusText = /失败|没有识别|超时/.test(message) ? "未听清" : "完成"
    }

    compactRepresentation: Item {
        implicitWidth: 40
        implicitHeight: 40
        Image {
            id: entryIcon
            anchors.centerIn: parent
            width: Math.max(16, Math.min(parent.width, parent.height) - 4)
            height: width
            source: Qt.resolvedUrl("assistant.svg")
            sourceSize.width: 96
            sourceSize.height: 96
            fillMode: Image.PreserveAspectFit
            smooth: true
            scale: entryMouse.containsMouse ? 1.06 : 1
            Behavior on scale { NumberAnimation { duration: 140 } }
        }
        MouseArea {
            id: entryMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root.expanded = !root.expanded
        }
    }

    fullRepresentation: Rectangle {
        id: panel
        implicitWidth: 480
        implicitHeight: root.historyExpanded ? 650 : 374
        Layout.minimumWidth: 440
        Layout.preferredWidth: 480
        Layout.maximumWidth: 600
        Layout.minimumHeight: implicitHeight
        Layout.maximumHeight: implicitHeight
        Layout.preferredHeight: implicitHeight
        color: "#f7faf8"
        radius: 20
        border.color: "#d8e5df"
        border.width: 1
        clip: true

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 22
            spacing: 12

            RowLayout {
                Layout.fillWidth: true
                spacing: 9
                Image { source: Qt.resolvedUrl("mark.svg"); Layout.preferredWidth: 23; Layout.preferredHeight: 23 }
                Text { text: "小希"; font.pixelSize: 18; font.weight: Font.DemiBold; color: root.ink }
                Text { text: "LOCAL ASSISTANT"; font.pixelSize: 9; font.letterSpacing: 0.8; color: root.muted }
                Item { Layout.fillWidth: true }
                Rectangle {
                    implicitWidth: statusLabel.implicitWidth + 18
                    implicitHeight: 26
                    radius: 9
                    color: root.isListening ? "#d9eee4" : "#e7efe9"
                    Text { id: statusLabel; anchors.centerIn: parent; text: root.statusText; font.pixelSize: 11; color: "#4e7465" }
                }
                SoftButton {
                    text: "×"; quiet: true; font.pixelSize: 20
                    implicitWidth: 26; implicitHeight: 28; leftPadding: 0; rightPadding: 0
                    Accessible.name: "收起助手"
                    onClicked: root.expanded = false
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: 4
                spacing: 12
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    Text { text: root.isListening ? "我在听，请说…" : "说一句，我来帮你"; font.pixelSize: 23; font.weight: Font.DemiBold; color: root.ink }
                    Text { text: "调节电脑，打开应用，或聊一聊。"; font.pixelSize: 12; color: root.muted }
                }
                Item { Layout.fillWidth: true }
                Controls.Button {
                    id: microphone
                    implicitWidth: 64
                    implicitHeight: 64
                    enabled: !root.isProcessing
                    Accessible.name: root.isListening ? "取消录音" : "开始语音输入"
                    Controls.ToolTip.visible: hovered
                    Controls.ToolTip.text: root.isListening ? "取消本次语音" : "点击说话"
                    background: Rectangle {
                        radius: 32
                        color: !microphone.enabled ? "#adc6b8" : root.isListening ? "#bd654d" : microphone.hovered ? "#18573f" : root.accent
                        Behavior on color { ColorAnimation { duration: 140 } }
                    }
                    contentItem: Item {
                        Image {
                            anchors.centerIn: parent
                            width: 28; height: 28
                            source: Qt.resolvedUrl(root.isListening ? "stop.svg" : "microphone.svg")
                        }
                    }
                    onClicked: root.isListening ? root.stopListening() : root.startListening()
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 7
                Repeater {
                    model: ["音量设为30%", "打开浏览器", "屏幕暗一点"]
                    delegate: SoftButton {
                        required property string modelData
                        text: modelData
                        font.pixelSize: 11
                        leftPadding: 10; rightPadding: 10
                        enabled: !root.busy
                        onClicked: root.sendQuery(modelData)
                    }
                }
                Item { Layout.fillWidth: true }
            }

            ListView {
                id: historyView
                visible: root.historyExpanded
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: root.historyExpanded ? 120 : 0
                clip: true
                spacing: 10
                model: conversation
                boundsBehavior: Flickable.StopAtBounds
                Controls.ScrollBar.vertical: Controls.ScrollBar { }
                onCountChanged: Qt.callLater(function() { historyView.positionViewAtEnd() })
                delegate: Rectangle {
                    required property string speaker
                    required property string body
                    width: historyView.width - 8
                    height: messageColumn.implicitHeight + 24
                    radius: 12
                    color: speaker === "user" ? "#e5efe9" : "#ffffff"
                    border.width: speaker === "user" ? 0 : 1
                    border.color: "#e2eae5"
                    Column {
                        id: messageColumn
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12 }
                        spacing: 5
                        Text { text: speaker === "user" ? "你" : "小希"; font.pixelSize: 10; font.weight: Font.DemiBold; color: "#7e9387" }
                        TextEdit {
                            width: parent.width
                            text: body
                            textFormat: TextEdit.PlainText
                            readOnly: true
                            selectByMouse: true
                            wrapMode: TextEdit.Wrap
                            font.pixelSize: 13
                            color: "#243b3b"
                            selectedTextColor: "#183e36"
                            selectionColor: "#c9e5d7"
                        }
                    }
                }
                Text {
                    anchors.centerIn: parent
                    visible: conversation.count === 0
                    text: "从一句简单的指令开始"
                    font.pixelSize: 12
                    color: "#91a297"
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 56
                color: "#ffffff"
                radius: 12
                border.width: 1
                border.color: inputField.activeFocus ? "#6b9f85" : "#d9e5dd"
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 6
                    spacing: 5
                    Controls.TextField {
                        id: inputField
                        Layout.fillWidth: true
                        enabled: !root.busy
                        text: root.draft
                        onTextEdited: root.draft = text
                        placeholderText: "也可以输入指令…"
                        font.pixelSize: 13
                        color: "#243b3b"
                        placeholderTextColor: "#899b92"
                        selectionColor: "#c9e5d7"
                        selectedTextColor: root.ink
                        background: Item {}
                        onAccepted: if (root.sendQuery(text)) root.draft = ""
                    }
                    SoftButton {
                        text: "发送"; primary: true
                        implicitHeight: 40
                        enabled: inputField.text.trim().length > 0 && !root.busy
                        onClicked: if (root.sendQuery(inputField.text)) root.draft = ""
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                Layout.minimumHeight: 34
                Layout.maximumHeight: 44
                text: root.feedback
                font.pixelSize: 11
                color: "#7b8e83"
                wrapMode: Text.Wrap
                maximumLineCount: 2
                elide: Text.ElideRight
            }
            Item { visible: !root.historyExpanded; Layout.fillHeight: true }

            RowLayout {
                Layout.fillWidth: true
                spacing: 3
                SoftButton {
                    text: root.historyExpanded ? "收起对话" : "展开对话"
                    quiet: true
                    font.pixelSize: 11
                    leftPadding: 2; rightPadding: 10
                    onClicked: root.historyExpanded = !root.historyExpanded
                }
                SoftButton {
                    text: "撤销调节"; quiet: true; font.pixelSize: 11
                    enabled: !root.busy
                    onClicked: root.sendQuery("撤销上次调节")
                }
                Item { Layout.fillWidth: true }
                SoftButton {
                    text: root.backendReady ? "本地连接" : "重新连接"
                    quiet: true; font.pixelSize: 11
                    enabled: !root.busy
                    onClicked: root.connectBackend()
                }
            }
        }
    }

    Timer {
        interval: 900
        running: root.isListening
        repeat: true
        onTriggered: {
            if (root.pollPending) return
            root.pollPending = true
            var epoch = root.voiceEpoch
            root.callBackend("getState", [], function(state) {
                root.pollPending = false
                if (epoch !== root.voiceEpoch || !root.isListening) return
                if (String(state) === "idle") {
                    root.isListening = false
                    root.callBackend("getVoiceResult", [], function(result) {
                        if (epoch === root.voiceEpoch) root.finishVoice(result)
                    })
                }
            }, function() { root.pollPending = false; root.isListening = false })
        }
    }
    Component.onCompleted: root.connectBackend()
}
