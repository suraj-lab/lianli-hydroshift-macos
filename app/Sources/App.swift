import SwiftUI

@main
struct HydroShiftApp: App {
    @State private var model = Model()

    var body: some Scene {
        MenuBarExtra {
            MenuView().environment(model)
        } label: {
            Text(model.title)
        }
        .menuBarExtraStyle(.window)

        Window("HydroShift Curves", id: "curves") {
            CurveEditorView().environment(model)
        }
        .defaultSize(width: 600, height: 640)

        Window("HydroShift Doctor", id: "doctor") {
            DoctorView()
        }
        .defaultSize(width: 660, height: 460)
    }
}

@Observable
final class Model {
    var status: Status?
    @ObservationIgnored private var timer: Timer?

    init() {
        poll()
        timer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            self?.poll()
        }
    }

    func poll() { status = Backend.readStatus() }

    var age: Double {
        guard let s = status else { return .infinity }
        return Date().timeIntervalSince1970 - s.ts
    }

    var warn: Bool { status?.telemetry != "ok" || age > 120 }

    var title: String {
        guard let c = status?.coolant_c, age < 300 else { return "🌡 —" }
        return String(format: "🌡 %.1f°%@", c, warn ? "⚠︎" : "")
    }
}

struct MenuView: View {
    @Environment(Model.self) private var model
    @Environment(\.openWindow) private var openWindow
    @State private var busy = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let s = model.status, model.age < 300 {
                let rpm = s.rpm ?? [0, 0, 0, 0]
                Text("Fan   \(rpm[0]) / \(rpm[1]) / \(rpm[2]) rpm  (pwm \(s.fan_pwm ?? 0)/255)")
                    .font(.system(.body, design: .monospaced))
                Text("Pump  \(rpm.count > 3 ? rpm[3] : 0) rpm  (target \(s.pump_target_rpm ?? 0))")
                    .font(.system(.body, design: .monospaced))
                Text("Telemetry \(s.telemetry ?? "?") · \(Int(model.age))s ago")
                    .foregroundStyle(model.warn ? .orange : .secondary)
            } else {
                Text("no daemon status").foregroundStyle(.red)
            }

            Divider()
            ThemePicker(current: model.status?.theme_index, busy: $busy)
            Divider()

            Button("Edit Curves…") { open("curves") }
            Button("Run Doctor") { open("doctor") }
            Button("Restart Daemon") {
                busy = true
                Task.detached {
                    _ = Backend.restartDaemon()
                    await MainActor.run { busy = false }
                }
            }
            Divider()
            Button("Quit") { NSApp.terminate(nil) }
        }
        .disabled(busy)
        .padding(12)
        .frame(width: 280)
    }

    private func open(_ id: String) {
        openWindow(id: id)
        NSApp.activate()
    }
}

struct ThemePicker: View {
    let current: Int?
    @Binding var busy: Bool
    @State private var selection: Int = -1

    var body: some View {
        Picker("Theme", selection: $selection) {
            if selection == -1 { Text("?").tag(-1) }
            ForEach(0...12, id: \.self) { Text("\($0)").tag($0) }
        }
        .onAppear { selection = current ?? -1 }
        .onChange(of: selection) { old, new in
            guard old != -1, new != old else { return }
            busy = true
            Task.detached {
                _ = Backend.applyTheme(new)
                await MainActor.run { busy = false }
            }
        }
    }
}

struct DoctorView: View {
    @State private var output = "running doctor…"

    var body: some View {
        VStack(alignment: .leading) {
            ScrollView {
                Text(output)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(8)
            }
            Button("Re-run") { run() }.padding([.horizontal, .bottom], 8)
        }
        .task { run() }
    }

    private func run() {
        output = "running doctor…"
        Task.detached {
            let out = Backend.runDoctor()
            await MainActor.run { output = out }
        }
    }
}
