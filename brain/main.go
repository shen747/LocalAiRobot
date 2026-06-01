// brain/main.go -- Local AI robot orchestrator (Pi 5 "brain").
package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"go.bug.st/serial"
)

// ---------- Config: env vars with sane defaults (no hardcoded /home/pi) ----------
type Config struct {
	OllamaURL  string
	Model      string
	WhisperBin string
	WhisperMod string
	PiperBin   string
	PiperVoice string
	MicDevice  string
	PicoPort   string
	RecordSecs string
	TmpDir     string
}

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func loadConfig() Config {
	home, _ := os.UserHomeDir()
	return Config{
		OllamaURL:  env("ROBOT_OLLAMA_URL", "http://localhost:11434/api/generate"),
		Model:      env("ROBOT_MODEL", "gemma3:4b"),
		WhisperBin: env("ROBOT_WHISPER_BIN", filepath.Join(home, "whisper.cpp/build/bin/whisper-cli")),
		WhisperMod: env("ROBOT_WHISPER_MODEL", filepath.Join(home, "whisper.cpp/models/ggml-base.en.bin")),
		PiperBin:   env("ROBOT_PIPER_BIN", filepath.Join(home, "piper/piper/piper")),
		PiperVoice: env("ROBOT_PIPER_VOICE", filepath.Join(home, "piper/en_US-lessac-medium.onnx")),
		MicDevice:  env("ROBOT_MIC", "plughw:1,0"),
		PicoPort:   env("ROBOT_PICO_PORT", "/dev/ttyACM0"),
		RecordSecs: env("ROBOT_RECORD_SECS", "4"),
		TmpDir:     env("ROBOT_TMPDIR", "/dev/shm"), // RAM-backed: no SD-card wear
	}
}

// ---------- Shared types ----------
type LLMResponse struct {
	Action     string `json:"action"`
	DurationMs int    `json:"duration_ms"`
	Speed      int    `json:"speed"`
	Reply      string `json:"reply"`
}

var validActions = map[string]bool{
	"FWD": true, "REV": true, "STRAFE_L": true, "STRAFE_R": true,
	"TURN_L": true, "TURN_R": true, "STOP": true,
}

// Common whisper hallucinations on silence/noise -- ignore these outright.
var whisperNoise = map[string]bool{
	"": true, ".": true, "you": true, "bye.": true, "thank you.": true,
	"thanks for watching.": true, "thank you for watching.": true,
	"[blank_audio]": true, "(silence)": true, "(buzzing)": true,
}

func isNoise(t string) bool { return whisperNoise[strings.ToLower(strings.TrimSpace(t))] }

func clamp(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// audioMu makes the physical audio path half-duplex: at most one of
// {record, playback} at a time. Kills the self-listening feedback loop AND
// prevents two aplay processes fighting over the device.
var audioMu sync.Mutex

func main() {
	cfg := loadConfig()

	port, err := serial.Open(cfg.PicoPort, &serial.Mode{BaudRate: 115200})
	if err != nil {
		log.Fatalf("open serial %s: %v (Pico plugged in? check ROBOT_PICO_PORT)", cfg.PicoPort, err)
	}
	defer port.Close()

	mover := newMover(port)       // owns ALL serial writes (no races)
	go readPico(cfg, port, mover) // reads Pico events; never writes the port

	reader := bufio.NewReader(os.Stdin)
	fmt.Println("Robot Brain online.")
	fmt.Println("Push-to-talk: press ENTER, speak after the prompt, then wait.")
	fmt.Println("Type 'q' then ENTER to quit.")

	for {
		fmt.Print("\n[ENTER]=talk  q=quit > ")
		line, _ := reader.ReadString('\n')
		if strings.TrimSpace(strings.ToLower(line)) == "q" {
			mover.stop()
			fmt.Println("Bye.")
			return
		}

		text, err := listenAndTranscribe(cfg)
		if err != nil {
			log.Printf("listen: %v", err)
			continue
		}
		text = strings.TrimSpace(text)
		if len(text) < 2 || isNoise(text) {
			fmt.Println("(ignored: empty / noise)")
			continue
		}
		fmt.Printf("Heard: %q\n", text)

		resp, err := queryOllama(cfg, text)
		if err != nil {
			log.Printf("llm: %v", err)
			mover.stop() // fail safe
			continue
		}
		fmt.Printf("Action=%s dur=%dms speed=%d reply=%q\n",
			resp.Action, resp.DurationMs, resp.Speed, resp.Reply)

		mover.submit(resp)     // non-blocking: motion runs in the mover goroutine
		speak(cfg, resp.Reply) // synchronous: returns when playback is done
	}
}

// ---------- Record + STT (half-duplex, with timeouts) ----------
func listenAndTranscribe(cfg Config) (string, error) {
	wav := filepath.Join(cfg.TmpDir, "command.wav")

	audioMu.Lock()
	rctx, rcancel := context.WithTimeout(context.Background(), 20*time.Second)
	recErr := exec.CommandContext(rctx, "arecord",
		"-D", cfg.MicDevice, "-d", cfg.RecordSecs,
		"-f", "S16_LE", "-r", "16000", "-c", "1", wav).Run()
	rcancel()
	audioMu.Unlock()
	if recErr != nil {
		return "", fmt.Errorf("arecord: %w", recErr)
	}

	wctx, wcancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer wcancel()
	var out bytes.Buffer
	cmd := exec.CommandContext(wctx, cfg.WhisperBin, "-m", cfg.WhisperMod, "-f", wav, "-nt")
	cmd.Stdout = &out
	if err := cmd.Run(); err != nil {
		return "", fmt.Errorf("whisper: %w", err)
	}
	return strings.TrimSpace(out.String()), nil
}

// ---------- LLM (forced JSON, with HTTP timeout) ----------
var httpClient = &http.Client{Timeout: 60 * time.Second}

func queryOllama(cfg Config, userText string) (LLMResponse, error) {
	var r LLMResponse
	system := `You are the brain of a mecanum robot car. Convert the user's spoken command into ONE JSON object and nothing else.
Keys:
- "action": EXACTLY one of FWD, REV, STRAFE_L, STRAFE_R, TURN_L, TURN_R, STOP.
- "duration_ms": integer 300-5000, how long to move (use 0 only for STOP).
- "speed": integer 0-100.
- "reply": a short spoken response, max 12 words, plain text only.
If the command is unclear or unsafe, use action STOP and say why in reply.
User command: ` + userText

	payload := map[string]any{
		"model":  cfg.Model,
		"prompt": system,
		"stream": false,
		"format": "json",
	}
	body, _ := json.Marshal(payload)
	resp, err := httpClient.Post(cfg.OllamaURL, "application/json", bytes.NewReader(body))
	if err != nil {
		return r, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)

	var wrap struct {
		Response string `json:"response"`
	}
	if err := json.Unmarshal(raw, &wrap); err != nil {
		return r, fmt.Errorf("ollama envelope: %w (raw: %s)", err, string(raw))
	}
	if err := json.Unmarshal([]byte(wrap.Response), &r); err != nil {
		return r, fmt.Errorf("model JSON %q: %w", wrap.Response, err)
	}
	return r, nil
}

// ---------- TTS (NO shell -> no injection; serialized playback) ----------
func speak(cfg Config, text string) {
	text = strings.TrimSpace(text)
	if text == "" {
		return
	}
	// Unique temp file so concurrent speaks don't clobber each other.
	f, err := os.CreateTemp(cfg.TmpDir, "reply-*.wav")
	if err != nil {
		log.Printf("tts temp: %v", err)
		return
	}
	wav := f.Name()
	f.Close()
	defer os.Remove(wav)

	// Synthesize: pipe text via stdin, invoke piper directly (no bash -c).
	sctx, scancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer scancel()
	piper := exec.CommandContext(sctx, cfg.PiperBin, "-m", cfg.PiperVoice, "--output_file", wav)
	piper.Stdin = strings.NewReader(text)
	if err := piper.Run(); err != nil {
		log.Printf("piper: %v", err)
		return
	}

	// Play back under the audio lock (serialized, never overlaps recording).
	audioMu.Lock()
	defer audioMu.Unlock()
	pctx, pcancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer pcancel()
	exec.CommandContext(pctx, "aplay", wav).Run()
}

// ---------- Mover: single goroutine owns all serial writes ----------
type moveCmd struct {
	action string
	speed  int
	dur    time.Duration
}

type Mover struct {
	port serial.Port
	ch   chan moveCmd
}

func newMover(port serial.Port) *Mover {
	m := &Mover{port: port, ch: make(chan moveCmd, 8)}
	go m.run()
	return m
}

func (m *Mover) run() {
	send := func(s string) { m.port.Write([]byte(s + "\n")) }
	var active string
	var deadline time.Time
	// Keep-alive faster than the Pico's 1500ms watchdog so motion is smooth.
	tick := time.NewTicker(700 * time.Millisecond)
	defer tick.Stop()
	for {
		select {
		case c := <-m.ch:
			if c.action == "STOP" || c.action == "" {
				active = ""
				send("STOP")
				continue
			}
			if c.speed > 0 {
				send(fmt.Sprintf("SPEED %d", c.speed))
			}
			active = c.action
			deadline = time.Now().Add(c.dur)
			send(active) // immediate first pulse
		case <-tick.C:
			if active == "" {
				continue
			}
			if time.Now().After(deadline) {
				active = ""
				send("STOP")
				continue
			}
			send(active) // refresh the Pico watchdog
		}
	}
}

// submit replaces any in-flight motion (a new command preempts the old one).
func (m *Mover) submit(r LLMResponse) {
	if !validActions[r.Action] {
		log.Printf("invalid action %q -> STOP", r.Action)
		r.Action = "STOP"
	}
	if r.Action == "STOP" {
		m.ch <- moveCmd{action: "STOP"}
		return
	}
	m.ch <- moveCmd{
		action: r.Action,
		speed:  clamp(r.Speed, 0, 100),
		dur:    time.Duration(clamp(r.DurationMs, 300, 5000)) * time.Millisecond,
	}
}

func (m *Mover) stop() { m.ch <- moveCmd{action: "STOP"} }

// ---------- Read Pico events (read-only on the port) ----------
func readPico(cfg Config, port serial.Port, mover *Mover) {
	sc := bufio.NewScanner(port)
	var lastObstacle time.Time
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		fmt.Printf("Pico: %s\n", line)
		if strings.HasPrefix(line, "EVT OBSTACLE") {
			mover.stop()
			if time.Since(lastObstacle) > 3*time.Second { // debounce TTS
				lastObstacle = time.Now()
				go speak(cfg, "Obstacle ahead. Stopping.")
			}
		}
	}
}
