#!/bin/sh
# 在 macOS 上生成本地 .app 包装（仅用于"登录项"显示名，路径全部便携，不写死用户名）。
# 生成的 ScheduledTasks.app/ 已被 .gitignore 忽略，不会进入仓库。
set -e

cd "$(dirname "$0")"
APP="ScheduledTasks.app"
mkdir -p "$APP/Contents/MacOS"

cat > "$APP/Contents/MacOS/定时任务集合" <<'EOF'
#!/bin/sh
# 相对定位到 scheduled-tasks/ 根目录，使用系统 python3（零依赖，仅标准库）
HERE="$(dirname "$0")"
ROOT="$(cd "$HERE/../../.." && pwd)"
exec python3 "$ROOT/run_all.py"
EOF
chmod +x "$APP/Contents/MacOS/定时任务集合"

cat > "$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>定时任务集合</string>
  <key>CFBundleDisplayName</key><string>定时任务集合</string>
  <key>CFBundleExecutable</key><string>定时任务集合</string>
  <key>CFBundleIdentifier</key><string>com.hejinlin.scheduled-tasks</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>LSUIElement</key><string>1</string>
</dict>
</plist>
EOF

echo "已生成本地 $APP（登录项显示名 = 定时任务集合）。"
echo "可在 系统设置 → 通用 → 登录项 中点击 + 添加它，实现开机/登录后自动跑 run_all.py。"
