# Updating

How to get new code onto the robot. Your settings live in `config/robot.env`, which git ignores, so an update
never touches them.

## Every update

```bash
cd ~/LocalAiRobot
git pull
./scripts/check_config.sh          # lists settings that are new in the template
./scripts/provision_face_id.sh     # fetches any new face models; instant when there are none
./scripts/run_me.sh --reload       # restart with the new code
```

Then read the start of the new log with `journalctl -u robot.service -n 100`.

Sometimes you'll also want to:

- **Start the next log clean.** `./scripts/purge_garbage.sh --dry-run` shows what would go, and
  `./scripts/purge_garbage.sh` deletes old captures, logs and caches. It keeps the face database. **Don't run it
  with `sudo`**: it asks for `sudo` itself where it needs to, and under `sudo` it looks in `/root` and deletes
  nothing.
- **Re-enrol faces** after changes to face recognition: `bash enroll_all.sh && ./scripts/rag.sh --faces`.
- **Wipe the memory database** with `./scripts/purge_garbage.sh --all`. It backs the database up first and asks
  you to type `WIPE`. You only need this if someone was enrolled from the camera and isn't in `enroll_all.sh`.

## Moving off the old local_branch setup

Robots set up before this change kept their settings in a private git branch called `local_branch`. Move to the
new layout once:

```bash
cd ~/LocalAiRobot
git diff --stat main local_branch        # files your branch changed; note any besides config/robot.env
cp config/robot.env ~/robot.env.mine     # your settings, kept safe outside the repo
git fetch origin
git checkout -f -B main origin/main      # switch to the new main; this removes the old tracked config file
cp ~/robot.env.mine config/robot.env     # your settings back; git ignores the file now
git branch -D local_branch
./scripts/check_config.sh
sudo systemctl restart robot
```

If the first command listed files other than `config/robot.env`, copy those out too before deleting the
branch: they only exist there.

## Backups

Git can bring back everything except two files: your settings and the robot's memory (faces included). Back
them up now and then, and copy the file off the robot:

```bash
tar czf ~/state-$(date +%F).tgz -C ~/LocalAiRobot config/robot.env -C ~ .localairobot/memory.db
```

The backup contains your keys, so keep it private. For full disk images, see
[troubleshooting](troubleshooting.md#recovery). Editing code on the robot and pushing it to GitHub? See
[development.md](development.md).
