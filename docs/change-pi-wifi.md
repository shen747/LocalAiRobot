# Changing the Pi's Wi-Fi in the field (no monitor, no keyboard)

> 🎯 Put the robot on a **new Wi-Fi** — or recover SSH access anywhere — using **just your phone**. All
> your *coding* still happens over SSH as usual; this is only about getting the Pi onto a network in the
> first place, without dragging out a monitor and keyboard every time.

**Works as-is on the default Raspberry Pi OS (64-bit)** — the OS this build uses. It's Debian **Bookworm**,
whose networking backend is **NetworkManager**, so every command below is native to it. The Pi 5 handles
the captive-portal routing in the background without any dent in your robotics code.

Two tiers, each set up once:

| Tier | What | When it saves you |
|---|---|---|
| **1 — Phone-hotspot safety net** | The Pi auto-joins your phone's hotspot | Guaranteed backdoor, zero extra software |
| **2 — Comitup captive portal** | The Pi hosts its own hotspot + a web page to pick a Wi-Fi | Onboarding onto a brand-new network you can't pre-save |

---

## Tier 1 — Safety net: save your phone's hotspot (do this **first**)

> 🛟 Set this up **before** touching any network service, so you always have a way back in.

The install scripts already did the **secret-free** groundwork for you — mDNS (`avahi`, so
`<hostname>.local` resolves), a **persistent journal**, and (if you passed `ROBOT_INSTALL_COMITUP=1`) the
Comitup portal. The one thing they **can't** do is store your hotspot's **password** — so you save the
hotspot by hand, **once**:

1. Turn on your phone's Wi-Fi hotspot.
2. SSH in and **save** it as a low-priority fallback. Use `connection add` — it only *stores* the profile, it
   does **not** switch the radio, so it can't cut the SSH session you're on:
   ```bash
   sudo nmcli connection add type wifi con-name "phone-hotspot" ssid "YourHotspotName" \
        wifi-sec.key-mgmt wpa-psk wifi-sec.psk "YourHotspotPassword" \
        connection.autoconnect yes connection.autoconnect-priority -10
   ```
   The password is typed at the terminal, **never** written to a repo file. The negative priority keeps your
   normal Wi-Fi preferred when both are in range.

> ⚠️ **Do NOT use `nmcli device wifi connect` from an SSH session on your *current* Wi-Fi** — it switches the
> radio **immediately** and freezes the very session you're on (the classic lockout). `connection add` above
> is the safe way; it never switches.

Now, **anywhere**: flip on your phone hotspot → the Pi auto-joins → SSH straight in
(`ssh <user>@<hostname>.local`, or the phone's connected-devices list for the IP).

> ⚠️ **Hotspot client isolation.** Many phone hotspots block device-to-device traffic — if yours does you
> **can't** SSH to the Pi over it, no matter what. iPhone Personal Hotspot generally doesn't isolate; some
> Androids do and won't let you disable it. Check before relying on it.

---

## Seeing & switching Wi-Fi by hand (`nmcli`)

NetworkManager knows two things — the networks **in range right now** and the networks it has **saved**
(and will auto-join). Your **home Wi-Fi is already saved** (you set it when flashing the card), and the
hotspot command above saved *that* too — so hopping between them never needs the password again.

```bash
# See what's in range right now  (add a rescan first if the list looks stale)
sudo nmcli device wifi rescan ; nmcli device wifi list

# See which networks are already SAVED, and what you're connected to right now
nmcli connection show            # all saved profiles (the NAME is usually the SSID)
nmcli device status              # what each interface is on  (wlan0 -> SSID)

# Go BACK to a saved network (e.g. home) — no password needed, it's already stored
sudo nmcli connection up "YourHomeSSID"

# Join a BRAND-NEW network (first time only; this saves it for next time)
sudo nmcli device wifi connect "NewSSID" password "ThePassword"

# Forget a network so it stops auto-joining
sudo nmcli connection delete "SomeSSID"
```

> 🔁 **Both home and the hotspot in range?** The simplest switch back is to just **turn the phone hotspot
> off** — the Pi falls back to home on its own. To make home *always* win, give it a higher priority:
> `sudo nmcli connection modify "YourHomeSSID" connection.autoconnect-priority 10` (higher = preferred).

> 💡 Prefer a menu over typing? **`sudo nmtui`** does all of the above with arrow keys — *Activate a
> connection* to switch networks, *Edit a connection* to set priority or delete one.

---

## Tier 2 — Captive portal: Comitup

Comitup hooks into NetworkManager. When it finds **no known network** (neither your home Wi-Fi nor your
saved hotspot), it spins up its **own** access point with a web portal where you pick the new network. It
stays out of the way otherwise — the AP only appears when you're stranded.

> 🤖 **Automated:** `ROBOT_INSTALL_COMITUP=1 ./scripts/install_stack_pi5.sh` (or `_jetson.sh`) installs
> Comitup and enables it for the next boot for you — no passwords involved (the portal collects the target
> Wi-Fi's password at runtime, nothing is stored). Or do it by hand:

1. Update, then install Comitup + Avahi (mDNS, so you can reach the Pi by **name** instead of hunting an IP):
   ```bash
   sudo apt update && sudo apt upgrade -y
   sudo apt install -y comitup avahi-daemon
   ```
2. Enable + start both services, then reboot to apply:
   ```bash
   sudo systemctl enable --now comitup avahi-daemon
   sudo reboot
   ```

> 🎨 The default portal is deliberately plain — but it serves ordinary local **HTML/CSS files**, so you can
> restyle it to match the robot later. Canonical setup + options live in the Comitup project
> (`davesteele/comitup` on GitHub).

### How it behaves in the field

1. Power on at a new location. The Pi scans for your **home Wi-Fi** → not found.
2. It scans for your **phone hotspot** (if it's on) → connects, done.
3. Otherwise **Comitup** broadcasts an AP named `comitup-<id>`.
4. Join that AP from your phone; the captive portal pops up automatically (if not, browse to
   `http://10.42.0.1` or `http://comitup-<id>.local`).
5. Pick the local Wi-Fi and enter its password. The Pi joins it and **drops its own AP**.
6. Open your terminal app → `ssh you@<hostname>.local` → you're in.

> 🔧 Already have a shell (via the hotspot or SSH)? You don't need the portal at all — use the
> **`nmcli`/`nmtui` cheat-sheet above** to list and switch networks directly.

---

## Connecting from your phone with ConnectBot (Android)

[ConnectBot](https://connectbot.org/) is a free, open-source SSH client — ideal for reaching the robot in the
field with no laptop. (On iPhone, **Termius** or **Blink** work the same way.)

1. **Install** ConnectBot (Play Store or F-Droid).
2. **Get on the same network as the Pi** — your home Wi-Fi, or your **phone hotspot** with the Pi joined to
   it (Tier 1 above).
3. **Add the robot as a host.** In ConnectBot's address box type `<user>@<hostname>.local`, e.g.
   `ramesh@LocalAiRobot-Jarvis.local`; leave protocol **ssh** / port **22**, then tap to connect.
   ```
   ramesh@LocalAiRobot-Jarvis.local
   ```
   > On a **phone hotspot**, `.local` (mDNS) sometimes doesn't resolve from Android — then use the Pi's **IP**
   > from the phone's connected-devices list instead: `ramesh@192.168.x.y`.
4. **First connect:** accept the host fingerprint, enter your Pi password → you're in. `cd ~/LocalAiRobot`
   and work as usual (new shells auto-load `config/robot.env`).
5. **Living in ConnectBot:** the on-screen key row gives you **Tab / Ctrl / Esc / arrows**; volume-down acts
   as **Ctrl** (volume-down then `c` = Ctrl-C). **Long-press the host → Edit** to add a nickname, a
   **post-login command** (e.g. `cd ~/LocalAiRobot`), or keep-alive so the session doesn't drop.

> 🔑 **Skip the password (recommended for the field).** Typing a password on a phone keyboard gets old fast —
> set up an Ed25519 key **once** (next section) and ConnectBot logs in instantly, over home Wi-Fi *or* the
> hotspot.

---

## Passwordless SSH from your phone (Ed25519 keys)

Typing an SSH password on a phone keyboard in the field is miserable. An **Ed25519** key pair fixes it:
your phone holds the private key, the Pi holds the public one, and they handshake instantly. It's the
modern standard — highly secure and short enough to manage easily. Do this in a mobile terminal app —
**Termius** or the open-source **ConnectBot** are both excellent on Android.

1. **Generate the key on your phone.** In the app's **Keychain / Pubkeys** section, create a new key,
   choose **Ed25519**, and leave the passphrase blank (for automatic login). Copy the **public** key
   (it starts with `ssh-ed25519`).
2. **Add it to the Pi** — one last password login:
   ```bash
   mkdir -p ~/.ssh
   nano ~/.ssh/authorized_keys      # paste the public key on a new line, then Ctrl-O, Enter, Ctrl-X
   ```
3. **Fix the permissions** — SSH ignores the key if the file is group/other-accessible:
   ```bash
   chmod 700 ~/.ssh
   chmod 600 ~/.ssh/authorized_keys
   ```
4. **Verify, *then* lock it down.** In a **new** tab, connect with the key and confirm it needs no
   password. Only once that works, disable password logins so the robot is safe on public networks:
   ```bash
   sudo nano /etc/ssh/sshd_config   # set:  PasswordAuthentication no   (uncomment the line, yes -> no)
   sudo systemctl restart ssh
   ```

> 🛟 **Before disabling passwords**, be sure the key login works **and** keep a fallback: your saved phone
> hotspot (Tier 1), and for the worst case a **USB-TTL serial console** on the GPIO UART. A typo in
> `sshd_config` can otherwise lock you out of a headless robot with no way back.

Now, the moment the Pi boots and joins your hotspot (or you join its Comitup portal), opening your
terminal app drops you **straight to a shell — no password**.

---

> ▶️ **Want the robot's brain to start on boot too** (no SSH at all)? Install the systemd boot service —
> `./scripts/install_stack_pi5.sh --bootstrap` (or `./scripts/run_me.sh --install-boot`). It's ordered
> **after the network is up**, so Wi-Fi/SSH and this portal are ready before the brain launches. Full
> details in the [assembly guide](assembly.md).

> 📜 **Reading the boot service's logs.** The install scripts make the systemd journal **persistent**
> (`/var/log/journal`), so it survives reboots. Live: `journalctl -u robot.service -f`. This boot:
> `journalctl -u robot.service -b`. The run **before** the last reboot (e.g. to see why it crashed):
> `journalctl -u robot.service -b -1`. (Set `ROBOT_PERSIST_JOURNAL=0` before installing to opt out.)
