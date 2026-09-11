import Foundation
import ImageIO
import Vision

// Lightweight command-line OCR used by the packaged screenshot Agent.
// Output format is intentionally kept compatible with capture.py:
// recognized text<TAB>x fraction<TAB>y-from-top fraction

func fail(_ message: String, code: Int32 = 2) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(code)
}

let arguments = CommandLine.arguments
guard arguments.count >= 2 else {
    fail("usage: ocr <image> [search]")
}

let imagePath = arguments[1]
let search = arguments.count >= 3 ? arguments[2] : ""
let imageURL = URL(fileURLWithPath: imagePath)
guard let source = CGImageSourceCreateWithURL(imageURL as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
    fail("cannot load image")
}

let request = VNRecognizeTextRequest { request, error in
    if let error = error {
        fail("OCR failed: \(error.localizedDescription)", code: 3)
    }
    guard let observations = request.results as? [VNRecognizedTextObservation] else { return }

    for observation in observations {
        guard let candidate = observation.topCandidates(1).first else { continue }
        let text = candidate.string
        if !search.isEmpty && !text.contains(search) {
            continue
        }
        let box = observation.boundingBox
        let yFromTop = max(0, min(1, 1 - box.maxY))
        let xFromLeft = max(0, min(1, box.minX))
        print("\(text)\t\(xFromLeft)\t\(yFromTop)")
    }
}
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "en-US"]
request.usesLanguageCorrection = true

do {
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
} catch {
    fail("OCR failed: \(error.localizedDescription)", code: 3)
}
