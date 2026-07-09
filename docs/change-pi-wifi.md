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

1. Turn on your phone's Wi-Fi hotspot.
2. SSH in (or use the monitor one last time) and save it — NetworkManager stores the profile permanently:
   ```bash
   sudo nmcli device wifi connect "YourHotspotName" password "YourHotspotPassword"
   ```

Now, **anywhere**: flip on your phone hotspot → the Pi auto-joins → SSH straight in.

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
