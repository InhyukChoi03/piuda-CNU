import Foundation

@MainActor
final class AppModel: ObservableObject {
    @Published var profile = Profile(userName: "사용자", caregiverName: "보호자", locale: "ko-KR")
    @Published var tasks: [TaskItem] = []
    @Published var risk = RiskAssessment.empty
    @Published var dashboard: DashboardResponse?
    @Published var isLoading = false
    @Published var isConnected = false
    @Published var errorMessage: String?

    private var userRefreshInProgress = false
    private var dashboardRefreshInProgress = false

    var serverURL: String {
        get { UserDefaults.standard.string(forKey: "piudaServerURL") ?? "http://192.168.4.1:8080" }
        set { UserDefaults.standard.set(newValue, forKey: "piudaServerURL") }
    }

    var caregiverToken: String? { KeychainStore.load() }
    var isCaregiverLoggedIn: Bool { caregiverToken != nil }

    private var client: APIClient { APIClient(baseURL: serverURL, token: caregiverToken) }

    func refreshUser() async {
        guard !userRefreshInProgress else { return }
        userRefreshInProgress = true
        isLoading = true
        defer {
            isLoading = false
            userRefreshInProgress = false
        }
        do {
            async let loadedProfile: Profile = client.request("/profile")
            async let loadedTasks: TaskListResponse = client.request("/tasks/today")
            async let loadedRisk: RiskAssessment = client.request("/risk/current")
            let (profile, taskResult, risk) = try await (loadedProfile, loadedTasks, loadedRisk)
            self.profile = profile
            self.tasks = taskResult.items
            self.risk = risk
            isConnected = true
            errorMessage = nil
        } catch {
            guard !isCancellation(error) else { return }
            isConnected = false
            errorMessage = error.localizedDescription
        }
    }

    func complete(task: TaskItem) async {
        do {
            let result: CompletionResponse = try await client.request("/tasks/\(task.id)/complete", method: "POST", body: [:])
            risk = result.risk
            await refreshUser()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func ask(_ message: String) async throws -> String {
        let result: FeedbackResponse = try await client.request("/feedback", method: "POST", body: ["message": message])
        risk = result.risk
        return result.reply
    }

    func login(pin: String) async -> Bool {
        do {
            let result: LoginResponse = try await APIClient(baseURL: serverURL, token: nil).request(
                "/auth/login", method: "POST", body: ["pin": pin, "device_name": "피우다 iOS"]
            )
            KeychainStore.save(token: result.token)
            await loadDashboard()
            guard dashboard != nil else {
                KeychainStore.clear()
                return false
            }
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    func loadDashboard() async {
        guard !dashboardRefreshInProgress else { return }
        guard let token = caregiverToken else {
            dashboard = nil
            return
        }
        dashboardRefreshInProgress = true
        defer { dashboardRefreshInProgress = false }
        let authenticatedClient = APIClient(baseURL: serverURL, token: token)
        do {
            let loadedDashboard: DashboardResponse = try await authenticatedClient.request("/dashboard")
            guard caregiverToken == token else { return }
            dashboard = loadedDashboard
            isConnected = true
            errorMessage = nil
        } catch {
            guard caregiverToken == token else { return }
            guard !isCancellation(error) else { return }
            dashboard = nil
            isConnected = false
            errorMessage = error.localizedDescription
        }
    }

    func acknowledge(alert: AlertItem) async {
        do {
            let _: SimpleResponse = try await client.request("/alerts/\(alert.id)/ack", method: "POST", body: [:])
            await loadDashboard()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func logout() async {
        let token = caregiverToken
        let authenticatedClient = token.map { APIClient(baseURL: serverURL, token: $0) }
        KeychainStore.clear()
        dashboard = nil
        guard let authenticatedClient else { return }
        do {
            let _: SimpleResponse = try await authenticatedClient.request(
                "/auth/logout",
                method: "POST",
                body: [:]
            )
        } catch {
            isConnected = false
            errorMessage = "이 기기에서는 로그아웃했지만 서버 토큰을 해제하지 못했습니다. \(error.localizedDescription)"
        }
    }

    func updateServerURL(_ value: String) async {
        serverURL = value.trimmingCharacters(in: .whitespacesAndNewlines)
        await refreshUser()
    }

    private func isCancellation(_ error: Error) -> Bool {
        error is CancellationError || (error as? URLError)?.code == .cancelled
    }
}

private struct SimpleResponse: Codable { let ok: Bool }
