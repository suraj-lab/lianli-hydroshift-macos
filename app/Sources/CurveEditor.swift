import SwiftUI

struct CurveEditorView: View {
    @Environment(Model.self) private var model
    @State private var fanPoints: [CGPoint] = []
    @State private var pumpPoints: [CGPoint] = []
    @State private var message = ""
    @State private var busy = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Fan curve — PWM (0–255) vs coolant °C").font(.headline)
            CurveCanvas(points: $fanPoints, xRange: 24...52, yRange: 0...255,
                        marker: model.status?.coolant_c)
            Text("Pump curve — RPM (1600–3200) vs coolant °C").font(.headline)
            CurveCanvas(points: $pumpPoints, xRange: 24...52, yRange: 1600...3200,
                        marker: model.status?.coolant_c)
            HStack {
                Button("Revert") { load() }
                Spacer()
                Text(message).foregroundStyle(.secondary)
                Button("Save & Apply") { save() }
                    .keyboardShortcut(.defaultAction)
            }
        }
        .disabled(busy)
        .padding(16)
        .onAppear { load() }
    }

    private func load() {
        let cfg = Backend.readConfig()
        fanPoints = curve(cfg["fan_curve"])
        pumpPoints = curve((cfg["pump"] as? [String: Any])?["curve"])
        message = fanPoints.isEmpty ? "could not read config" : ""
    }

    private func curve(_ raw: Any?) -> [CGPoint] {
        (raw as? [[Double]] ?? []).compactMap {
            $0.count == 2 ? CGPoint(x: $0[0], y: $0[1]) : nil
        }
    }

    private func save() {
        busy = true
        message = "applying…"
        let fan = fanPoints, pump = pumpPoints
        Task.detached {
            let ok = Backend.applyCurves(fan: fan, pump: pump)
            await MainActor.run {
                busy = false
                message = ok ? "applied ✓" : "reload failed — config saved, restart daemon manually"
            }
        }
    }
}

/// Drag-to-edit curve canvas. Drag points vertically/horizontally; x is clamped
/// between neighbouring points so the curve stays monotonic in temperature.
// ponytail: drag-only editor — add/remove points in config.json if ever needed
struct CurveCanvas: View {
    @Binding var points: [CGPoint]
    let xRange: ClosedRange<Double>
    let yRange: ClosedRange<Double>
    var marker: Double?
    @State private var dragIndex: Int?

    private let inset = CGSize(width: 44, height: 22)

    var body: some View {
        GeometryReader { geo in
            let size = geo.size
            Canvas { ctx, _ in
                draw(ctx: ctx, size: size)
            }
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { v in drag(v.location, size: size) }
                    .onEnded { _ in dragIndex = nil }
            )
        }
        .frame(minHeight: 190)
        .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 6))
    }

    // MARK: coordinate mapping

    private func plotRect(_ size: CGSize) -> CGRect {
        CGRect(x: inset.width, y: 8,
               width: size.width - inset.width - 12,
               height: size.height - inset.height - 8)
    }

    private func toView(_ p: CGPoint, _ size: CGSize) -> CGPoint {
        let r = plotRect(size)
        let fx = (p.x - xRange.lowerBound) / (xRange.upperBound - xRange.lowerBound)
        let fy = (p.y - yRange.lowerBound) / (yRange.upperBound - yRange.lowerBound)
        return CGPoint(x: r.minX + fx * r.width, y: r.maxY - fy * r.height)
    }

    private func toData(_ v: CGPoint, _ size: CGSize) -> CGPoint {
        let r = plotRect(size)
        let fx = (v.x - r.minX) / r.width
        let fy = (r.maxY - v.y) / r.height
        return CGPoint(
            x: (xRange.lowerBound + fx * (xRange.upperBound - xRange.lowerBound))
                .clamped(to: xRange),
            y: (yRange.lowerBound + fy * (yRange.upperBound - yRange.lowerBound))
                .clamped(to: yRange))
    }

    // MARK: drawing

    private func draw(ctx: GraphicsContext, size: CGSize) {
        let r = plotRect(size)

        // grid + axis labels
        let xStep = 4.0
        var x = (xRange.lowerBound / xStep).rounded(.up) * xStep
        while x <= xRange.upperBound {
            let vx = toView(CGPoint(x: x, y: yRange.lowerBound), size).x
            ctx.stroke(Path { $0.move(to: CGPoint(x: vx, y: r.minY)); $0.addLine(to: CGPoint(x: vx, y: r.maxY)) },
                       with: .color(.gray.opacity(0.2)), lineWidth: 0.5)
            ctx.draw(Text("\(Int(x))").font(.caption2).foregroundStyle(.secondary),
                     at: CGPoint(x: vx, y: r.maxY + 10))
            x += xStep
        }
        for frac in [0.0, 0.25, 0.5, 0.75, 1.0] {
            let yVal = yRange.lowerBound + frac * (yRange.upperBound - yRange.lowerBound)
            let vy = toView(CGPoint(x: xRange.lowerBound, y: yVal), size).y
            ctx.stroke(Path { $0.move(to: CGPoint(x: r.minX, y: vy)); $0.addLine(to: CGPoint(x: r.maxX, y: vy)) },
                       with: .color(.gray.opacity(0.2)), lineWidth: 0.5)
            ctx.draw(Text("\(Int(yVal))").font(.caption2).foregroundStyle(.secondary),
                     at: CGPoint(x: r.minX - 22, y: vy))
        }

        // live coolant marker
        if let m = marker, xRange.contains(m) {
            let vx = toView(CGPoint(x: m, y: yRange.lowerBound), size).x
            ctx.stroke(Path { $0.move(to: CGPoint(x: vx, y: r.minY)); $0.addLine(to: CGPoint(x: vx, y: r.maxY)) },
                       with: .color(.orange.opacity(0.7)), style: StrokeStyle(lineWidth: 1, dash: [4, 3]))
        }

        // curve + handles
        guard !points.isEmpty else {
            ctx.draw(Text("no curve loaded").foregroundStyle(.secondary),
                     at: CGPoint(x: r.midX, y: r.midY))
            return
        }
        var path = Path()
        path.move(to: toView(points[0], size))
        for p in points.dropFirst() { path.addLine(to: toView(p, size)) }
        ctx.stroke(path, with: .color(.accentColor), lineWidth: 2)

        for (i, p) in points.enumerated() {
            let v = toView(p, size)
            let radius: CGFloat = i == dragIndex ? 7 : 5
            ctx.fill(Path(ellipseIn: CGRect(x: v.x - radius, y: v.y - radius,
                                            width: radius * 2, height: radius * 2)),
                     with: .color(.accentColor))
        }
        if let i = dragIndex {
            let p = points[i]
            ctx.draw(Text("\(Int(p.x.rounded()))°, \(Int(p.y.rounded()))")
                        .font(.caption).foregroundStyle(.primary),
                     at: toView(p, size).applying(.init(translationX: 0, y: -16)))
        }
    }

    // MARK: interaction

    private func drag(_ loc: CGPoint, size: CGSize) {
        if dragIndex == nil {
            var best: (Int, CGFloat)?
            for (i, p) in points.enumerated() {
                let v = toView(p, size)
                let d = hypot(v.x - loc.x, v.y - loc.y)
                if d < 14, d < (best?.1 ?? .infinity) { best = (i, d) }
            }
            dragIndex = best?.0
        }
        guard let i = dragIndex else { return }
        var p = toData(loc, size)
        // keep x strictly between neighbours so the curve stays sorted
        if i > 0 { p.x = max(p.x, points[i - 1].x + 0.5) }
        if i < points.count - 1 { p.x = min(p.x, points[i + 1].x - 0.5) }
        points[i] = p
    }
}

private extension Double {
    func clamped(to range: ClosedRange<Double>) -> Double {
        Swift.min(Swift.max(self, range.lowerBound), range.upperBound)
    }
}
