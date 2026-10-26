import Foundation
import Security
import Vision
import AppKit
let input = FileHandle.standardInput.readDataToEndOfFile()
do {
    let args = try JSONSerialization.jsonObject(with: input) as! [String:Any]
    let op = args["op"] as! String
    var result: Any = NSNull()
    if op == "ocr" {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = false
        let handler = VNImageRequestHandler(url: URL(fileURLWithPath: args["path"] as! String))
        try handler.perform([request])
        result = request.results?.compactMap { $0.topCandidates(1).first?.string } ?? []
    } else {
        let query: [String:Any] = [kSecClass as String:kSecClassGenericPassword,
          kSecAttrService as String:args["service"]!,kSecAttrAccount as String:args["account"]!]
        var status: OSStatus = errSecSuccess
        if op == "set" {
            let data = try JSONSerialization.data(withJSONObject: args["value"]!)
            status = SecItemUpdate(query as CFDictionary, [kSecValueData as String:data] as CFDictionary)
            if status == errSecItemNotFound {
                var add = query; add[kSecValueData as String] = data
                add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
                status = SecItemAdd(add as CFDictionary,nil)
            }
        } else if op == "delete" {
            status = SecItemDelete(query as CFDictionary)
        } else if op == "get" {
            var get = query;get[kSecReturnData as String] = true;get[kSecMatchLimit as String] = kSecMatchLimitOne
            var data: CFTypeRef?; status = SecItemCopyMatching(get as CFDictionary,&data)
            if status == errSecSuccess, let bytes = data as? Data {result = try JSONSerialization.jsonObject(with: bytes)}
        } else {throw NSError(domain:"Unknown operation",code:1)}
        if status != errSecSuccess && status != errSecItemNotFound {throw NSError(domain:NSOSStatusErrorDomain,code:Int(status))}
    }
    let output = try JSONSerialization.data(withJSONObject:result,options:[.fragmentsAllowed,.sortedKeys])
    FileHandle.standardOutput.write(output)
} catch {FileHandle.standardError.write(Data("Native operation failed\n".utf8)); exit(1)}
