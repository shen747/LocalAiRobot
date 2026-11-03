# Guides

Every page in the repo, one line each. New here? Read [install.md](install.md) first.

## Build and set up

- [install.md](install.md): blank SD card to a robot that talks back
- [parts.md](parts.md): what to buy, and the printable enclosures
- [assembly.md](assembly.md): build the crawler one tested step at a time
- [wiring/pwm-esc-servo-wiring.md](wiring/pwm-esc-servo-wiring.md): PCA9685, ESC and steering servo, pin by pin
- [wiring/ultrasonic-sensors-wiring.md](wiring/ultrasonic-sensors-wiring.md): the three distance sensors
- [wiring/pir-sensor-wiring.md](wiring/pir-sensor-wiring.md): the greeter's motion sensor
- [calibration.md](calibration.md): find your ESC, steering and pronunciation values on the bench
- [../scripts/patches/README.md](../scripts/patches/README.md): one-command settings for each hardware option

## Modes

- [crawler.md](crawler.md): drives, explores and finds things on voice command (the default)
- [concierge.md](concierge.md): a stationary greeter that recognises people
- [halloween.md](halloween.md): a Concierge extra that scares trick-or-treaters
- [zigbee-pir.md](zigbee-pir.md): add a wireless motion sensor to the greeter

## Features

- [weather.md](weather.md): the 3-day forecast card and weather-aware greetings
- [social.md](social.md): greetings that remember when they last saw you and what you said
- [http_setup.md](http_setup.md): command the robot from your phone
- [gemini-setup.md](gemini-setup.md): use Google Gemini as a faster, online brain
- [openwakeword/openwake-word.md](openwakeword/openwake-word.md): how the wake word works, and training your own
- [../tools/wakeword-training/README.md](../tools/wakeword-training/README.md): the wake-word training tools
- [../brain/faces/README.md](../brain/faces/README.md): design the robot's face
- [../sounds/README.md](../sounds/README.md): the short spoken sounds and the Halloween stingers
- [../models/README.md](../models/README.md): the models committed to the repo

## Run and maintain

- [configuration.md](configuration.md): how settings work, the ones people change, and secrets
- [../config/README.md](../config/README.md): every setting, with its default
- [updating.md](updating.md): get new code without losing your settings
- [troubleshooting.md](troubleshooting.md): symptoms and fixes for sound, wake word, camera, PIR and more
- [diagnostics.md](diagnostics.md): what the self-test checks and how to read it
- [change-pi-wifi.md](change-pi-wifi.md): move to a new Wi-Fi network or get back in without a screen

## Change the code

- [development.md](development.md): where everything lives, running the tests, adding a feature
- [../tests/README.md](../tests/README.md): what each test checks
