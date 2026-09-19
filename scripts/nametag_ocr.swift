import Foundation
import Vision
import AppKit

// Text reader for Stage 3c (Nametag). Compiled on first use by 03c-nametag.py:
//   swiftc -O scripts/nametag_ocr.swift -o raw/nametag/bin/ocr
// usage: ocr <image> [<image> ...]
// prints JSON: [{"f": file name, "items": [{"t": text, "c": confidence, "x","y","w","h"}]}]
// with x/y/w/h normalized to the image and the origin at the top-left.
// Uses Apple's Vision framework, so it runs on this Mac with no packages and
// nothing leaves the machine.
var out: [[String: Any]] = []
for path in CommandLine.arguments.dropFirst() {
    guard let img = NSImage(contentsOfFile: path),
          let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { continue }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.usesLanguageCorrection = false   // names, not prose
    try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
    var items: [[String: Any]] = []
    for o in req.results ?? [] {
        guard let c = o.topCandidates(1).first else { continue }
        let b = o.boundingBox
        items.append(["t": c.string, "c": c.confidence, "x": b.minX, "y": 1 - b.maxY, "w": b.width, "h": b.height])
    }
    out.append(["f": (path as NSString).lastPathComponent, "items": items])
}
print(String(data: try! JSONSerialization.data(withJSONObject: out), encoding: .utf8)!)
