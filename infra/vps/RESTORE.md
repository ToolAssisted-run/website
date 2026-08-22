# Restoring toolassisted.run from a Swiss Backup snapshot

Everything needed lives in two rclone remotes (`swissbackup:` — Infomaniak
Swiss Backup over Swift). A fresh Ubuntu LTS VPS plus one day's snapshot
restores the whole site.

Fetch the newest of each:

    rclone copy swissbackup:site-backups/archive-<DAY>.bundle .
    rclone copy swissbackup:site-backups/website-<DAY>.bundle .
    rclone copy swissbackup:site-backups/state-<DAY>.tar.gz .
    rclone copy swissbackup:discourse-backups/<newest>.tar.gz .
    rclone copy swissbackup:corpus-backups/tasvideos-<DAY>.bundle .   # optional

Then:

1. **State first**: `sudo tar -xzf state-<DAY>.tar.gz -C /` restores
   `/opt/archivist` keys and operational files, `/etc/archivist.env`,
   the systemd unit, nginx vhosts, certificates, cron files and
   `/var/discourse/containers/app.yml`.
2. **Repos**: `git clone archive-<DAY>.bundle /opt/archivist/archive` and
   `git clone website-<DAY>.bundle /opt/archivist/website`; point their
   `origin` at GitHub again (`git remote set-url origin …`). Copy
   `website/archivist/*.py` into `/opt/archivist/` and
   `systemctl enable --now archivist` — its startup build republishes the
   site into `/opt/archivist/site/current`.
3. **nginx**: `apt install nginx`, symlink the restored vhosts from
   `sites-available` into `sites-enabled` (the tarball also restores
   `conf.d/` with the Cloudflare real-IP ranges and `snippets/` with the
   hardening headers the vhosts include), `systemctl reload nginx`.
   Certificates came with the tarball; certbot resumes renewals.
4. **Discourse**: standard install (`git clone
   https://github.com/discourse/discourse_docker /var/discourse`), the
   restored `app.yml` in place, `./launcher rebuild app`, then restore the
   dump from the admin panel or `discourse restore <file>` inside the
   container (put it in `shared/standalone/backups/default/` first).
5. **DNS**: point `toolassisted.run`, `www` and `forum` A records at the
   new machine; GitHub Pages remains the hot standby throughout.

The GitHub repos (archive, website, tasvideos-dumps mirror) are the primary
copies; these bundles exist so that a snapshot alone suffices even if GitHub
is unreachable.
