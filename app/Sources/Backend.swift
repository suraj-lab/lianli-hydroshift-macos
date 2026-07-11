import Foundation

/// Daemon telemetry snapshot, written by the daemon to status.json every tick.
struct Status: Codable {
    var ts: Double = 0
    var coolant_c: Double?
    var raw_coolant_c: Double?
    var stale_s: Double?
    var telemetry: String?
    var failsafe: Bool?
    var fan_pwm: Int?
    var pump_target_rpm: Int?
    var rpm: [Int]?
    var theme_index: Int?
}

/// File IO + daemon control. Paths hardcoded like the SwiftBar plugin: this is
/// a single-machine personal tool.
enum Backend {
    static let configDir = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".config/lianli-hydroshift")
    static let projectDir = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Projects/lianli-hydroshift-macos")
    static let label = "com.suraj.lianli-hydroshift"

    static func readStatus() -> Status? {
        guard let data = try? Data(contentsOf: configDir.appendingPathComponent("status.json"))
        else { return nil }
        return try? JSONDecoder().decode(Status.self, from: data)
    }

    /// Config is read/written as a raw dict so keys the app doesn't know survive.
    static func readConfig() -> [String: Any] {
        guard let data = try? Data(contentsOf: configDir.appendingPathComponent("config.json")),
              let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return [:] }
        return dict
    }

    @discardableResult
    static func writeConfig(_ dict: [String: Any]) -> Bool {
        guard let data = try? JSONSerialization.data(
            withJSONObject: dict, options: [.prettyPrinted, .sortedKeys])
        else { return false }
        return (try? data.write(to: configDir.appendingPathComponent("config.json"))) != nil
    }

    /// Admin-privilege shell via osascript — same UX as scripts/restart-daemon.sh.
    /// Blocking; call off the main thread.
    private static func adminShell(_ cmd: String) -> Bool {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        p.arguments = ["-e", "do shell script \"\(cmd)\" with administrator privileges"]
        do { try p.run() } catch { return false }
        p.waitUntilExit()
        return p.terminationStatus == 0
    }

    static func reloadDaemon() -> Bool {
        adminShell("launchctl kill SIGHUP system/\(label)")
    }

    static func restartDaemon() -> Bool {
        adminShell("launchctl kickstart -k system/\(label)")
    }

    static func runDoctor() -> String {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = [projectDir.appendingPathComponent("scripts/doctor.sh").path]
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        do { try p.run() } catch { return "failed to run doctor: \(error)" }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return String(data: data, encoding: .utf8) ?? "(no output)"
    }

    /// Write theme_index (clamped 0-12) and SIGHUP the daemon.
    static func applyTheme(_ index: Int) -> Bool {
        var cfg = readConfig()
        cfg["theme_index"] = max(0, min(12, index))
        guard writeConfig(cfg) else { return false }
        return reloadDaemon()
    }

    /// Write both curves and SIGHUP the daemon. Points are (temp, value).
    static func applyCurves(fan: [CGPoint], pump: [CGPoint]) -> Bool {
        var cfg = readConfig()
        cfg["fan_curve"] = fan.map { [Int($0.x.rounded()), Int($0.y.rounded())] }
        var pumpCfg = cfg["pump"] as? [String: Any] ?? [:]
        pumpCfg["curve"] = pump.map { [Int($0.x.rounded()), Int($0.y.rounded())] }
        cfg["pump"] = pumpCfg
        guard writeConfig(cfg) else { return false }
        return reloadDaemon()
    }
}
