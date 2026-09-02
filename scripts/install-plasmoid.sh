#!/usr/bin/env bash
# install-plasmoid.sh - 安装 LLM Assistant Plasma Widget
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLASMOID_SRC="$HOME/ai/plasmoids/llm-assistant"
DBUS_SERVICE_DIR="$HOME/.local/share/dbus-1/services"
DBUS_SERVICE_FILE="$DBUS_SERVICE_DIR/org.kde.plasma.llm-assistant.service"

echo "🤖 安装 LLM Assistant Plasma Widget..."

# 1. 检查依赖
echo "🔍 检查依赖..."
if ! python3 -c "import dbus" 2>/dev/null; then
    echo "❌ 需要安装 python-dbus:"
    echo "   sudo pacman -S --needed python-dbus"
    exit 1
fi
if ! python3 -c "from gi.repository import GLib" 2>/dev/null; then
    echo "❌ 需要安装 python-gobject:"
    echo "   sudo pacman -S --needed python-gobject"
    exit 1
fi

# 2. 创建 DBus 服务目录
echo "📁 创建 DBus 服务目录..."
mkdir -p "$DBUS_SERVICE_DIR"

# 3. 写入 DBus 服务文件
echo "📝 写入 DBus 服务文件..."
cat > "$DBUS_SERVICE_FILE" << EOF
[D-BUS Service]
Name=org.kde.plasma.llm-assistant
Exec=$SCRIPT_DIR/llm-assistant-backend.py
EOF

# 4. 设置脚本权限
echo "🔐 设置脚本权限..."
chmod +x "$SCRIPT_DIR/llm-assistant-backend.py"

# 5. 安装 Plasmoid
echo "📦 安装 Plasmoid..."
if command -v kpackagetool6 &>/dev/null; then
    kpackagetool6 -t Plasma/Applet -i "$PLASMOID_SRC"
    echo "   Plasmoid 已通过 kpackagetool6 安装"
elif command -v plasmapkg2 &>/dev/null; then
    plasmapkg2 -t Plasma/Applet -i "$PLASMOID_SRC"
    echo "   Plasmoid 已通过 plasmapkg2 安装"
else
    echo "⚠️  kpackagetool6/plasmapkg2 未找到，尝试手动安装..."
    TARGET="$HOME/.local/share/plasma/plasmoids/org.kde.plasma.llm-assistant"
    mkdir -p "$TARGET"
    cp -r "$PLASMOID_SRC/contents" "$TARGET/"
    cp "$PLASMOID_SRC/metadata.desktop" "$TARGET/"
    echo "   已复制到 $TARGET"
fi

echo ""
echo "✅ 安装完成！"
echo ""
echo "使用方式："
echo "1. 重启 Plasma:  qdbus org.kde.KLauncher /Shell reparseConfiguration"
echo "   或者注销重新登录"
echo "2. 添加 Widget:  右键桌面 → 添加 Widget → 搜索 'LLM Assistant'"
echo "3. 后端会通过 DBus 自动启动，无需手动运行"
echo ""
echo "手动启动后端（如需调试）:"
echo "   python3 $SCRIPT_DIR/llm-assistant-backend.py"
