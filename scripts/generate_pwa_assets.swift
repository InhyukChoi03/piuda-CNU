import AppKit
import CoreImage
import Foundation

let outputRoot = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
    .appendingPathComponent("piuda/static")
let iconRoot = outputRoot.appendingPathComponent("icons")
let qrRoot = outputRoot.appendingPathComponent("qr")
try FileManager.default.createDirectory(at: iconRoot, withIntermediateDirectories: true)
try FileManager.default.createDirectory(at: qrRoot, withIntermediateDirectories: true)

func writePNG(
    _ image: NSImage,
    to url: URL,
    interpolation: NSImageInterpolation = .high,
    opaque: Bool = false
) throws {
    let width = Int(image.size.width)
    let height = Int(image.size.height)
    guard let bitmap = NSBitmapImageRep(
        bitmapDataPlanes: nil,
        pixelsWide: width,
        pixelsHigh: height,
        bitsPerSample: 8,
        samplesPerPixel: opaque ? 3 : 4,
        hasAlpha: !opaque,
        isPlanar: false,
        colorSpaceName: .deviceRGB,
        bytesPerRow: 0,
        bitsPerPixel: 0
    ) else {
        throw NSError(domain: "PiudaAssets", code: 1)
    }
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    NSGraphicsContext.current?.imageInterpolation = interpolation
    image.draw(in: NSRect(x: 0, y: 0, width: width, height: height))
    NSGraphicsContext.restoreGraphicsState()
    guard let data = bitmap.representation(using: .png, properties: [:]) else {
        throw NSError(domain: "PiudaAssets", code: 1)
    }
    try data.write(to: url)
}

func makeIcon(size: Int) -> NSImage {
    let canvas = CGFloat(size)
    let image = NSImage(size: NSSize(width: canvas, height: canvas))
    image.lockFocus()

    NSColor(calibratedRed: 0.114, green: 0.420, blue: 0.329, alpha: 1).setFill()
    NSBezierPath(rect: NSRect(x: 0, y: 0, width: canvas, height: canvas)).fill()

    let stem = NSBezierPath()
    stem.move(to: NSPoint(x: canvas * 0.5, y: canvas * 0.22))
    stem.curve(to: NSPoint(x: canvas * 0.51, y: canvas * 0.55), controlPoint1: NSPoint(x: canvas * 0.48, y: canvas * 0.35), controlPoint2: NSPoint(x: canvas * 0.54, y: canvas * 0.42))
    stem.lineWidth = canvas * 0.055
    stem.lineCapStyle = .round
    NSColor(calibratedRed: 0.945, green: 0.914, blue: 0.773, alpha: 1).setStroke()
    stem.stroke()

    NSColor(calibratedRed: 0.555, green: 0.761, blue: 0.663, alpha: 1).setFill()
    let leafLeft = NSBezierPath()
    leafLeft.move(to: NSPoint(x: canvas * 0.49, y: canvas * 0.38))
    leafLeft.curve(to: NSPoint(x: canvas * 0.26, y: canvas * 0.46), controlPoint1: NSPoint(x: canvas * 0.39, y: canvas * 0.51), controlPoint2: NSPoint(x: canvas * 0.28, y: canvas * 0.52))
    leafLeft.curve(to: NSPoint(x: canvas * 0.49, y: canvas * 0.38), controlPoint1: NSPoint(x: canvas * 0.25, y: canvas * 0.36), controlPoint2: NSPoint(x: canvas * 0.38, y: canvas * 0.32))
    leafLeft.fill()

    let leafRight = NSBezierPath()
    leafRight.move(to: NSPoint(x: canvas * 0.52, y: canvas * 0.32))
    leafRight.curve(to: NSPoint(x: canvas * 0.75, y: canvas * 0.41), controlPoint1: NSPoint(x: canvas * 0.63, y: canvas * 0.44), controlPoint2: NSPoint(x: canvas * 0.74, y: canvas * 0.48))
    leafRight.curve(to: NSPoint(x: canvas * 0.52, y: canvas * 0.32), controlPoint1: NSPoint(x: canvas * 0.76, y: canvas * 0.31), controlPoint2: NSPoint(x: canvas * 0.64, y: canvas * 0.26))
    leafRight.fill()

    let petalColor = NSColor(calibratedRed: 0.973, green: 0.941, blue: 0.827, alpha: 1)
    petalColor.setFill()
    let petalSize = canvas * 0.19
    let flowerCenter = NSPoint(x: canvas * 0.5, y: canvas * 0.68)
    for index in 0..<5 {
        let angle = CGFloat(index) * 2 * .pi / 5 + .pi / 2
        let center = NSPoint(x: flowerCenter.x + cos(angle) * canvas * 0.105,
                             y: flowerCenter.y + sin(angle) * canvas * 0.105)
        NSBezierPath(ovalIn: NSRect(x: center.x - petalSize / 2, y: center.y - petalSize / 2, width: petalSize, height: petalSize)).fill()
    }
    NSColor(calibratedRed: 0.930, green: 0.690, blue: 0.220, alpha: 1).setFill()
    let centerSize = canvas * 0.15
    NSBezierPath(ovalIn: NSRect(x: flowerCenter.x - centerSize / 2, y: flowerCenter.y - centerSize / 2, width: centerSize, height: centerSize)).fill()

    image.unlockFocus()
    return image
}

func makeQR(_ value: String) throws -> NSImage {
    guard let filter = CIFilter(name: "CIQRCodeGenerator") else {
        throw NSError(domain: "PiudaAssets", code: 2)
    }
    filter.setValue(Data(value.utf8), forKey: "inputMessage")
    filter.setValue("M", forKey: "inputCorrectionLevel")
    guard let base = filter.outputImage else {
        throw NSError(domain: "PiudaAssets", code: 3)
    }

    let scale = floor(448 / base.extent.width)
    let rendered = base.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
    let context = CIContext(options: [.useSoftwareRenderer: false])
    guard let qrCG = context.createCGImage(rendered, from: rendered.extent) else {
        throw NSError(domain: "PiudaAssets", code: 4)
    }

    let canvas = NSImage(size: NSSize(width: 512, height: 512))
    canvas.lockFocus()
    NSColor.white.setFill()
    NSBezierPath(rect: NSRect(x: 0, y: 0, width: 512, height: 512)).fill()
    NSGraphicsContext.current?.imageInterpolation = .none
    let side = CGFloat(qrCG.width)
    NSImage(cgImage: qrCG, size: NSSize(width: side, height: side)).draw(
        in: NSRect(x: (512 - side) / 2, y: (512 - side) / 2, width: side, height: side),
        from: .zero,
        operation: .copy,
        fraction: 1
    )
    canvas.unlockFocus()
    return canvas
}

func decodedQR(at url: URL) throws -> String {
    guard let image = CIImage(contentsOf: url),
          let detector = CIDetector(
            ofType: CIDetectorTypeQRCode,
            context: nil,
            options: [CIDetectorAccuracy: CIDetectorAccuracyHigh]
          ),
          let feature = detector.features(in: image).first as? CIQRCodeFeature,
          let value = feature.messageString else {
        throw NSError(domain: "PiudaAssets", code: 5)
    }
    return value
}

for size in [180, 192, 512] {
    try writePNG(makeIcon(size: size), to: iconRoot.appendingPathComponent("icon-\(size).png"))
}
let iosAppIconRoot = outputRoot
    .deletingLastPathComponent()
    .deletingLastPathComponent()
    .appendingPathComponent("ios/Piuda/Piuda/Assets.xcassets/AppIcon.appiconset")
try FileManager.default.createDirectory(at: iosAppIconRoot, withIntermediateDirectories: true)
try writePNG(
    makeIcon(size: 1024),
    to: iosAppIconRoot.appendingPathComponent("AppIcon-1024.png"),
    opaque: true
)
let userURL = "http://CNU.local:8080/"
let caregiverURL = "https://CNU.local:8443/caregiver"
let userQR = qrRoot.appendingPathComponent("user.png")
let caregiverQR = qrRoot.appendingPathComponent("caregiver.png")
try writePNG(makeQR(userURL), to: userQR, interpolation: .none)
try writePNG(makeQR(caregiverURL), to: caregiverQR, interpolation: .none)
guard try decodedQR(at: userQR) == userURL,
      try decodedQR(at: caregiverQR) == caregiverURL else {
    throw NSError(domain: "PiudaAssets", code: 6)
}

print("Generated PWA icons and QR codes in \(outputRoot.path)")
