#!/usr/bin/env bash
# Build HydroShift.app from the Swift package.
# Usage: ./build-app.sh   then: open HydroShift.app  (or copy to /Applications)
set -euo pipefail
cd "$(dirname "$0")"

swift build -c release

APP=HydroShift.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp .build/release/HydroShift "$APP/Contents/MacOS/HydroShift"

cat > "$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key><string>com.suraj.hydroshift</string>
    <key>CFBundleName</key><string>HydroShift</string>
    <key>CFBundleExecutable</key><string>HydroShift</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>LSMinimumSystemVersion</key><string>14.0</string>
    <key>LSUIElement</key><true/>
</dict>
</plist>
EOF

codesign --force --sign - "$APP"
echo "Built $APP"
