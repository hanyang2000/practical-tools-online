// 基于 macOS Vision 框架的 OCR 工具
// 用法: ocr <图片路径> [搜索关键词]
//   输出每行: 文本\t归一化x\t归一化y(顶)\t归一化w\t归一化h
//   y 已翻转为「从上往下」，便于直接用 像素Y = 归一化y * 图片高度 计算。
// 编译: swiftc -O ocr.swift -o ocr
import Foundation
import Vision
import AppKit

let args = CommandLine.arguments
guard args.count >= 2 else {
    FileHandle.standardError.write("usage: ocr <image> [search]\n".data(using: .utf8)!)
    exit(1)
}
let path = args[1]
let search = args.count >= 3 ? args[2] : ""

guard let img = NSImage(contentsOfFile: path),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("cannot load image\n".data(using: .utf8)!)
    exit(2)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en"]
request.usesLanguageCorrection = true

let handler = VNImageRequestHandler(cgImage: cg, options: [:])
try handler.perform([request])

guard let results = request.results else { exit(0) }
for obs in results {
    guard let c = obs.topCandidates(1).first else { continue }
    let text = c.string
    if !search.isEmpty && !text.contains(search) { continue }
    let b = obs.boundingBox
    let yTop = 1.0 - b.origin.y - b.height  // 从上往下
    print("\(text)\t\(b.origin.x)\t\(yTop)\t\(b.width)\t\(b.height)")
}
