import Foundation

struct Profile: Codable {
    var userName: String
    var caregiverName: String
    var locale: String
}

struct TaskListResponse: Codable {
    let date: String
    let items: [TaskItem]
    let summary: TaskSummary
}

struct TaskSummary: Codable {
    let total: Int
    let completed: Int
    let missed: Int
}

struct TaskItem: Codable, Identifiable {
    let id: Int
    let dueAt: String
    let status: String
    let title: String
    let category: String
    let instructions: String?
    let scheduledTime: String

    var isCompleted: Bool { status == "completed" }
    var isMissed: Bool { status == "missed" }
}

struct RiskAssessment: Codable {
    let id: Int?
    let score: Int
    let level: String
    let levelLabel: String
    let factors: [RiskFactor]
    let assessedAt: String
    let newAlert: Bool?

    static let empty = RiskAssessment(
        id: nil,
        score: 0,
        level: "unknown",
        levelLabel: "확인 중",
        factors: [],
        assessedAt: "",
        newAlert: false
    )
}

struct RiskFactor: Codable, Identifiable {
    let code: String
    let label: String
    let points: Int
    let evidence: String?
    var id: String { code }
}

struct CompletionResponse: Codable {
    let ok: Bool
    let risk: RiskAssessment
}

struct LoginResponse: Codable {
    let ok: Bool
    let token: String
}

struct FeedbackResponse: Codable {
    let reply: String
    let speak: Bool
    let risk: RiskAssessment
}

struct DashboardResponse: Codable {
    let profile: Profile?
    let tasks: [TaskItem]
    let risk: RiskAssessment
    let alerts: [AlertItem]
    let sensorEvents: [SensorEvent]
}

struct AlertItem: Codable, Identifiable {
    let id: Int
    let level: String
    let title: String
    let message: String
    let createdAt: String
    let acknowledgedAt: String?
}

struct SensorEvent: Codable, Identifiable {
    let id: Int
    let eventType: String
    let value: Double?
    let confidence: Double?
    let occurredAt: String
    let deviceName: String
    let location: String
}

struct APIErrorPayload: Codable {
    let error: String?
    let message: String?
}
