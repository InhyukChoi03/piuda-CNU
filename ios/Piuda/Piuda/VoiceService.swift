import AVFoundation
import Speech

@MainActor
final class VoiceService: ObservableObject {
    @Published var transcript = ""
    @Published var isListening = false
    @Published var errorMessage: String?

    private let audioEngine = AVAudioEngine()
    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "ko-KR"))
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var inputTapInstalled = false
    private var audioSessionIsActive = false
    private var pendingStartID: UUID?
    private let synthesizer = AVSpeechSynthesizer()

    private func requestSpeechPermission() async -> SFSpeechRecognizerAuthorizationStatus {
        await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status)
            }
        }
    }

    private func requestMicrophonePermission() async -> Bool {
        if AVAudioApplication.shared.recordPermission == .granted {
            return true
        }
        return await withCheckedContinuation { continuation in
            AVAudioApplication.requestRecordPermission { granted in
                continuation.resume(returning: granted)
            }
        }
    }

    func start() async {
        stop()
        let startID = UUID()
        pendingStartID = startID
        synthesizer.stopSpeaking(at: .immediate)
        transcript = ""
        errorMessage = nil

        let speechPermission = await requestSpeechPermission()
        guard pendingStartID == startID else { return }
        guard speechPermission == .authorized else {
            errorMessage = speechPermission == .denied
                ? "음성 인식 권한이 필요해요. 설정 > 개인정보 보호 및 보안 > 음성 인식에서 피우다를 허용해 주세요."
                : "이 기기에서는 피우다의 음성 인식을 사용할 수 없어요."
            return
        }

        let microphoneAllowed = await requestMicrophonePermission()
        guard pendingStartID == startID else { return }
        guard microphoneAllowed else {
            errorMessage = "마이크 권한이 필요해요. 설정 > 개인정보 보호 및 보안 > 마이크에서 피우다를 허용해 주세요."
            return
        }

        guard let recognizer, recognizer.isAvailable else {
            errorMessage = "현재 한국어 음성 인식을 사용할 수 없어요. 잠시 후 다시 시도해 주세요."
            return
        }

        let audioSession = AVAudioSession.sharedInstance()
        do {
            // 녹음이 끝난 뒤 같은 화면에서 답변 TTS를 바로 재생하므로
            // record 전용 category를 남기지 않고 스피커 출력도 가능한 세션을 씁니다.
            try audioSession.setCategory(
                .playAndRecord,
                mode: .measurement,
                options: [.duckOthers, .defaultToSpeaker]
            )
            try audioSession.setActive(true, options: .notifyOthersOnDeactivation)
            audioSessionIsActive = true
        } catch {
            errorMessage = "마이크를 시작하지 못했어요. \(error.localizedDescription)"
            stop()
            return
        }

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        if recognizer.supportsOnDeviceRecognition { request.requiresOnDeviceRecognition = true }
        self.request = request

        let input = audioEngine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            errorMessage = "사용할 수 있는 마이크를 찾지 못했어요."
            stop()
            return
        }
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in request.append(buffer) }
        inputTapInstalled = true
        audioEngine.prepare()
        do {
            try audioEngine.start()
            pendingStartID = nil
            isListening = true
            task = recognizer.recognitionTask(with: request) { [weak self] result, error in
                Task { @MainActor in
                    guard let self, self.isListening else { return }
                    if let result {
                        self.transcript = result.bestTranscription.formattedString
                    }
                    if error != nil {
                        self.errorMessage = "음성을 인식하지 못했어요. 다시 말해 주세요."
                        self.stop()
                    } else if result?.isFinal == true {
                        self.stop()
                    }
                }
            }
        } catch {
            errorMessage = "마이크를 시작하지 못했어요. \(error.localizedDescription)"
            stop()
        }
    }

    func stop() {
        pendingStartID = nil
        isListening = false
        audioEngine.stop()
        if inputTapInstalled {
            audioEngine.inputNode.removeTap(onBus: 0)
            inputTapInstalled = false
        }
        let activeRequest = request
        let activeTask = task
        task = nil
        request = nil
        activeRequest?.endAudio()
        activeTask?.cancel()
        if audioSessionIsActive {
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            audioSessionIsActive = false
        }
    }

    func speak(_ text: String) {
        stop()
        synthesizer.stopSpeaking(at: .immediate)
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: "ko-KR")
        utterance.rate = 0.46
        synthesizer.speak(utterance)
    }
}
