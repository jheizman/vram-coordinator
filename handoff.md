# VRAM Coordinator Handoff

## Project identity
- GitHub repo: `https://github.com/jheizman/vram-coordinator`
- Host: `aibox` (`aibox.heizman.net`)
- Service user: `vram-coordinator`
- Service home: `/home/vram-coordinator`
- Repo path on host: `/home/vram-coordinator/vram-coordinator`

## Current architecture direction
- Coordinator is a dedicated host-level service.
- Coordinator owns VRAM admission decisions via `acquire` / `release`.
- Policy model includes soft/hard VRAM floors, priority tiers, back-pressure, and load-shedding.
- APIGateway role is signal emission only.

## Provisioning state (completed)
- Unix user exists: `vram-coordinator`
- User is in `docker` group.
- Repo is created and cloned under the service home path.
- Bootstrap commit in repo: `a854128` (`chore: bootstrap coordinator runtime files`)
- Bootstrap files in repo include:
  - `docker-compose.yml`
  - `.env.example`
  - `.gitignore`
  - README runtime section

## SSH access details
- `authorized_keys` is installed for the service account at:
  - `/home/vram-coordinator/.ssh/authorized_keys`
- Permissions:
  - `/home/vram-coordinator/.ssh` => `700`
  - `authorized_keys` => `600`
- Local SSH alias configured on operator machine:

```sshconfig
Host aibox-vram-coordinator
    HostName aibox.heizman.net
    User vram-coordinator
    IdentityFile ~/.ssh/findrai_linux
```

- Read-only SSH test result:
  - `ssh aibox-vram-coordinator 'id -un'` -> `vram-coordinator` (exit 0)

## Operating instruction
- Review-only context handoff.
- Do **not** implement or deploy changes until explicitly directed.
- Keep all coordinator work scoped to this repo/home:
  - `/home/vram-coordinator/vram-coordinator`
