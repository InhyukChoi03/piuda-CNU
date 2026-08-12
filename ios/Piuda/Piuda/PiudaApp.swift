import SwiftUI

@main
struct PiudaApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
                .tint(Color.piudaGreen)
        }
    }
}

extension Color {
    static let piudaGreen = Color(red: 0.11, green: 0.42, blue: 0.33)
    static let piudaPaper = Color(red: 0.96, green: 0.95, blue: 0.91)
}
