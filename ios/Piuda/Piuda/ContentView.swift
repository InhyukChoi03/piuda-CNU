import SwiftUI

private enum AppTab: Hashable {
    case today
    case assistant
    case caregiver
    case settings
}

private struct RefreshContext: Hashable {
    let tab: AppTab
    let isActive: Bool
}

struct ContentView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    @State private var selectedTab: AppTab = .today

    var body: some View {
        TabView(selection: $selectedTab) {
            TodayView()
                .tabItem { Label("오늘", systemImage: "checklist") }
                .tag(AppTab.today)
            AssistantView()
                .tabItem { Label("도우미", systemImage: "sparkles") }
                .tag(AppTab.assistant)
            CaregiverView()
                .tabItem { Label("보호자", systemImage: "person.2.fill") }
                .tag(AppTab.caregiver)
            SettingsView()
                .tabItem { Label("설정", systemImage: "gearshape.fill") }
                .tag(AppTab.settings)
        }
        .overlay(alignment: .top) {
            if let message = model.errorMessage {
                Text(message)
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)
                    .background(.red.opacity(0.9), in: Capsule())
                    .padding(.top, 8)
                    .onTapGesture { model.errorMessage = nil }
            }
        }
        .task(id: RefreshContext(tab: selectedTab, isActive: scenePhase == .active)) {
            guard scenePhase == .active else { return }
            while !Task.isCancelled {
                switch selectedTab {
                case .today:
                    await model.refreshUser()
                case .caregiver:
                    if model.isCaregiverLoggedIn {
                        await model.loadDashboard()
                    }
                case .assistant, .settings:
                    return
                }

                do {
                    try await Task.sleep(nanoseconds: 15_000_000_000)
                } catch {
                    return
                }
            }
        }
    }
}

struct TodayView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 18) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(Date.now.formatted(.dateTime.month(.wide).day().weekday(.wide)))
                            .font(.caption.weight(.bold)).foregroundStyle(Color.piudaGreen)
                        Text("\(model.profile.userName)님,\n오늘도 천천히 해봐요.")
                            .font(.system(size: 35, weight: .bold, design: .rounded))
                            .tracking(-1.2)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)

                    RiskHero(risk: model.risk)

                    HStack {
                        Text("오늘 할 일").font(.title2.bold())
                        Spacer()
                        Text("\(model.tasks.filter(\.isCompleted).count) / \(model.tasks.count) 완료")
                            .font(.subheadline).foregroundStyle(.secondary)
                    }

                    if model.tasks.isEmpty && !model.isLoading {
                        ContentUnavailableView("오늘 일정이 없습니다", systemImage: "calendar", description: Text("새 일정은 보호자 화면에서 등록할 수 있습니다."))
                            .frame(minHeight: 220)
                    } else {
                        ForEach(model.tasks) { task in
                            TaskRow(task: task) { Task { await model.complete(task: task) } }
                        }
                    }
                }
                .padding()
            }
            .background(Color.piudaPaper.ignoresSafeArea())
            .navigationTitle("피우다")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    HStack(spacing: 6) {
                        Circle().fill(model.isConnected ? .green : .red).frame(width: 8, height: 8)
                        Text(model.isConnected ? "Pi 연결" : "연결 안 됨").font(.caption)
                    }
                }
            }
            .refreshable { await model.refreshUser() }
        }
    }
}

struct RiskHero: View {
    let risk: RiskAssessment

    private var color: Color {
        switch risk.level {
        case "caution": .orange
        case "danger": Color(red: 0.78, green: 0.31, blue: 0.14)
        case "emergency": .red
        case "unknown": .gray
        default: Color.piudaGreen
        }
    }

    var body: some View {
        HStack(spacing: 20) {
            VStack(alignment: .leading, spacing: 7) {
                Text("현재 생활 상태").font(.caption.bold()).opacity(0.75)
                Text(risk.levelLabel).font(.system(size: 32, weight: .bold, design: .rounded))
                Text(risk.assessedAt.isEmpty ? "Pi에서 상태 정보를 불러오고 있습니다." : risk.factors.first?.label ?? "현재 확인된 위험 요인이 없습니다.")
                    .font(.subheadline).opacity(0.82)
            }
            Spacer()
            ZStack {
                Circle().fill(.white.opacity(0.14)).frame(width: 86, height: 86)
                VStack(spacing: 0) { Text(risk.assessedAt.isEmpty ? "—" : "\(risk.score)").font(.title.bold()); Text("점").font(.caption) }
            }
        }
        .foregroundStyle(.white)
        .padding(24)
        .background(color.gradient, in: RoundedRectangle(cornerRadius: 25, style: .continuous))
        .shadow(color: color.opacity(0.17), radius: 18, y: 8)
    }
}

struct TaskRow: View {
    let task: TaskItem
    let complete: () -> Void

    var body: some View {
        HStack(spacing: 15) {
            Text(task.scheduledTime)
                .font(.subheadline.bold()).foregroundStyle(Color.piudaGreen)
                .frame(width: 48)
            VStack(alignment: .leading, spacing: 4) {
                Text(task.title).font(.headline).strikethrough(task.isCompleted)
                Text(task.instructions ?? (task.isMissed ? "예정 시간이 지났습니다." : "일정 내용을 확인해 주세요."))
                    .font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
            Spacer()
            if task.isCompleted {
                Image(systemName: "checkmark.circle.fill").font(.title2).foregroundStyle(Color.piudaGreen)
            } else {
                Button("완료", action: complete)
                    .buttonStyle(.borderedProminent)
                    .tint(task.isMissed ? .orange : Color.piudaGreen)
                    .controlSize(.large)
            }
        }
        .padding(16)
        .background(.white.opacity(task.isCompleted ? 0.58 : 0.95), in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 20).stroke(task.isMissed ? .orange.opacity(0.35) : .clear))
    }
}

struct AssistantView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var voice = VoiceService()
    @State private var message = ""
    @State private var reply = "오늘 할 일을 물어보거나 어려운 점을 말해 주세요."
    @State private var waiting = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 22) {
                Spacer()
                Image(systemName: "sparkles")
                    .font(.system(size: 42, weight: .semibold)).foregroundStyle(.white)
                    .frame(width: 88, height: 88)
                    .background(Color.piudaGreen.gradient, in: RoundedRectangle(cornerRadius: 28))
                Text("생활 도우미").font(.largeTitle.bold())
                Text(reply)
                    .font(.title3).multilineTextAlignment(.center).lineSpacing(5)
                    .padding(.horizontal)
                if let errorMessage = voice.errorMessage {
                    Label(errorMessage, systemImage: "exclamationmark.circle.fill")
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal)
                }
                Spacer()
                HStack(spacing: 10) {
                    TextField("예: 지금 뭘 해야 해?", text: $message, axis: .vertical)
                        .textFieldStyle(.roundedBorder).lineLimit(1...3)
                    Button {
                        Task { voice.isListening ? voice.stop() : await voice.start() }
                    } label: {
                        Image(systemName: voice.isListening ? "waveform.circle.fill" : "mic.circle.fill").font(.system(size: 34))
                    }
                    Button {
                        Task { await send() }
                    } label: {
                        Image(systemName: "arrow.up.circle.fill").font(.system(size: 34))
                    }
                    .disabled(message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || waiting)
                }
                .padding()
                .background(.white, in: RoundedRectangle(cornerRadius: 22))
            }
            .padding()
            .background(Color.piudaPaper.ignoresSafeArea())
            .navigationTitle("도우미")
            .onChange(of: voice.transcript) { _, newValue in message = newValue }
            .onChange(of: scenePhase) { _, newPhase in
                if newPhase != .active { voice.stop() }
            }
            .onDisappear { voice.stop() }
        }
    }

    private func send() async {
        let question = message.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty else { return }
        voice.stop()
        waiting = true
        reply = "생각하고 있어요…"
        do {
            reply = try await model.ask(question)
            message = ""
            voice.speak(reply)
        } catch {
            reply = "지금은 답하기 어려워요. 잠시 후 다시 말해 주세요."
            model.errorMessage = error.localizedDescription
        }
        waiting = false
    }
}

struct CaregiverView: View {
    @EnvironmentObject private var model: AppModel
    @State private var pin = ""

    var body: some View {
        NavigationStack {
            Group {
                if let dashboard = model.dashboard {
                    List {
                        Section {
                            RiskHero(risk: dashboard.risk).listRowInsets(EdgeInsets()).listRowBackground(Color.clear)
                        }
                        Section("판단 근거") {
                            if dashboard.risk.factors.isEmpty { Text("현재 위험 요인이 없습니다.").foregroundStyle(.secondary) }
                            ForEach(dashboard.risk.factors) { factor in
                                HStack { VStack(alignment: .leading) { Text(factor.label); Text(factor.evidence ?? "현재 데이터 기준").font(.caption).foregroundStyle(.secondary) }; Spacer(); Text("-\(factor.points)").bold().foregroundStyle(.orange) }
                            }
                        }
                        Section("오늘 일정") { ForEach(dashboard.tasks) { task in HStack { Text(task.scheduledTime).foregroundStyle(Color.piudaGreen); Text(task.title); Spacer(); Text(task.isCompleted ? "완료" : task.isMissed ? "미수행" : "예정").font(.caption.bold()).foregroundStyle(task.isMissed ? .orange : .secondary) } } }
                        Section("최근 알림") {
                            if dashboard.alerts.isEmpty { Text("최근 알림이 없습니다.").foregroundStyle(.secondary) }
                            ForEach(dashboard.alerts) { alert in
                                VStack(alignment: .leading, spacing: 6) {
                                    Text(alert.title).bold()
                                    Text(alert.message).font(.caption).foregroundStyle(.secondary)
                                    if alert.acknowledgedAt == nil { Button("처리 확인") { Task { await model.acknowledge(alert: alert) } }.font(.caption.bold()) }
                                }
                            }
                        }
                        Section("최근 센서 기록") {
                            if dashboard.sensorEvents.isEmpty { Text("아직 센서 기록이 없습니다.").foregroundStyle(.secondary) }
                            ForEach(dashboard.sensorEvents.prefix(10)) { event in HStack { Image(systemName: event.eventType == "csi_fall" ? "exclamationmark.triangle.fill" : "wave.3.right").foregroundStyle(event.eventType == "csi_fall" ? .red : Color.piudaGreen); VStack(alignment: .leading) { Text(event.location); Text(event.eventType).font(.caption).foregroundStyle(.secondary) } } }
                        }
                        Section { Button("보호자 로그아웃", role: .destructive) { Task { await model.logout() } } }
                    }
                    .refreshable { await model.loadDashboard() }
                } else {
                    VStack(spacing: 20) {
                        Spacer()
                        Image(systemName: "person.2.fill").font(.system(size: 44)).foregroundStyle(Color.piudaGreen)
                        Text("보호자 확인").font(.largeTitle.bold())
                        Text("Pi에서 설정한 보호자 PIN을 입력하세요.").foregroundStyle(.secondary)
                        SecureField("PIN 4~12자리", text: $pin).keyboardType(.numberPad).textFieldStyle(.roundedBorder).frame(maxWidth: 280)
                        Button("대시보드 열기") { Task { if await model.login(pin: pin) { pin = "" } } }.buttonStyle(.borderedProminent).controlSize(.large).disabled(pin.count < 4)
                        Spacer()
                    }
                    .padding()
                }
            }
            .background(Color.piudaPaper.ignoresSafeArea())
            .navigationTitle("보호자")
        }
    }
}

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @State private var serverURL = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("Raspberry Pi 로컬 연결") {
                    TextField("http://CNU.local:8080", text: $serverURL)
                        .textInputAutocapitalization(.never).keyboardType(.URL)
                    Button("연결 저장 및 확인") { Task { await model.updateServerURL(serverURL) } }
                    LabeledContent("상태", value: model.isConnected ? "연결됨" : "연결 안 됨")
                }
                Section("개인정보") {
                    Label("영상과 음성 원본은 서버에 저장하지 않습니다.", systemImage: "video.slash.fill")
                    Label("일정과 센서 이벤트는 Pi 내부 SQLite에 저장합니다.", systemImage: "internaldrive.fill")
                }
                Section("도움말") { Text("iPhone과 Raspberry Pi가 같은 Wi-Fi에 있어야 합니다. 연결이 안 되면 Pi의 IP 주소를 입력할 수 있습니다.") }
            }
            .navigationTitle("설정")
            .onAppear { serverURL = model.serverURL }
        }
    }
}
